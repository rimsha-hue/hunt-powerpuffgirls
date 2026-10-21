"""The admin console: account management, the platform overview and the audit log.

Every admin action is audited under the acting admin (actor "admin", the target's id in
`entity_id`), so the record survives even when the target account is deleted. Nothing here reads
a user's resume, CV, ledger or email content.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Application, Audit, AuthSession, EmailLog, Job, SkillPlan, User
from app.domain.access import Account, AdminAction, Role, refusal, should_be_admin
from app.services import audit

SHIPPED = ("sent", "submitted")
WEEK = timedelta(days=7)
DAY = timedelta(hours=24)


class AdminError(Exception):
    """Safe to show to the admin."""


@dataclass(frozen=True)
class UserRow:
    user: User
    jobs: int
    applications_sent: int


@dataclass(frozen=True)
class Overview:
    users: int
    verified: int
    admins: int
    suspended: int
    new_this_week: int
    active_this_week: int
    applications_sent_this_week: int
    plans_verified: int
    scout_runs_today: int
    emails_today: dict[str, int]


@dataclass(frozen=True)
class AuditRow:
    entry: Audit
    email: str | None


def account(user: User, demo_email: str | None) -> Account:
    return Account(
        id=user.id,
        role=user.role,
        suspended=user.suspended_at is not None,
        verified=user.email_verified,
        demo=bool(demo_email) and user.email == (demo_email or "").lower(),
    )


async def active_admins(db: AsyncSession) -> int:
    count = await db.scalar(
        select(func.count(User.id)).where(User.role == "admin", User.suspended_at.is_(None))
    )
    return int(count or 0)


def grant_if_listed(db: AsyncSession, user: User, admin_emails: Collection[str]) -> bool:
    """Makes a listed, verified account an admin (ADMIN_EMAILS). True when it changed."""
    if not should_be_admin(user.email, user.email_verified, user.role, admin_emails):
        return False
    user.role = "admin"
    audit.record(
        db,
        user_id=user.id,
        actor="system",
        action="admin_granted",
        entity="user",
        entity_id=user.id,
        reason="admin_emails",
    )
    return True


async def grant_listed(db: AsyncSession, admin_emails: Collection[str]) -> int:
    """At startup: every listed, verified account that isn't an admin yet becomes one."""
    if not admin_emails:
        return 0
    users = await db.execute(select(User).where(User.email.in_(list(admin_emails))))
    return sum(grant_if_listed(db, user, admin_emails) for user in users.scalars())


async def act(
    db: AsyncSession,
    admin: User,
    target: User,
    action: AdminAction,
    *,
    demo_email: str | None,
    now: datetime,
    new_role: Role | None = None,
) -> None:
    """Checks the rules (domain/access.py), applies the action and audits it."""
    reason = refusal(
        action,
        actor_id=admin.id,
        target=account(target, demo_email),
        active_admins=await active_admins(db),
        new_role=new_role,
    )
    if reason is not None:
        raise AdminError(reason)
    target_id = target.id
    match action:
        case "suspend":
            target.suspended_at = now
            await db.execute(delete(AuthSession).where(AuthSession.user_id == target_id))
        case "restore":
            target.suspended_at = None
        case "sign_out":
            await db.execute(delete(AuthSession).where(AuthSession.user_id == target_id))
        case "set_role":
            assert new_role is not None
            target.role = new_role
        case "delete":
            # ON DELETE CASCADE removes every row the account owns.
            await db.execute(delete(User).where(User.id == target_id))
        case "resend_verification":
            pass  # the API sends the email; here it is only checked and audited
    audit.record(
        db,
        user_id=admin.id,
        actor="admin",
        action=f"admin_{action}",
        entity="user",
        entity_id=target_id,
        **({"role": new_role} if new_role else {}),
    )


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def list_users(
    db: AsyncSession, *, query: str, limit: int, offset: int
) -> tuple[list[UserRow], int]:
    jobs = select(func.count(Job.id)).where(Job.user_id == User.id).scalar_subquery()
    sent = (
        select(func.count(Application.id))
        .where(Application.user_id == User.id, Application.status.in_(SHIPPED))
        .scalar_subquery()
    )
    where = []
    if query.strip():
        pattern = _like(query.strip().lower())
        where.append(
            or_(
                func.lower(User.email).like(pattern, escape="\\"),
                func.lower(User.name).like(pattern, escape="\\"),
            )
        )
    total = await db.scalar(select(func.count(User.id)).where(*where))
    rows = await db.execute(
        select(User, jobs, sent)
        .where(*where)
        .order_by(User.created_at.desc(), User.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [UserRow(u, int(j or 0), int(s or 0)) for u, j, s in rows.all()], int(total or 0)


async def _count(db: AsyncSession, query: object) -> int:
    return int((await db.execute(query)).scalar_one() or 0)  # type: ignore[call-overload]


async def overview(db: AsyncSession, now: datetime) -> Overview:
    users = select(func.count(User.id))
    emails = await db.execute(
        select(EmailLog.kind, func.count(EmailLog.id))
        .where(EmailLog.created_at >= now - DAY)
        .group_by(EmailLog.kind)
    )
    return Overview(
        users=await _count(db, users),
        verified=await _count(db, users.where(User.email_verified_at.is_not(None))),
        admins=await _count(db, users.where(User.role == "admin")),
        suspended=await _count(db, users.where(User.suspended_at.is_not(None))),
        new_this_week=await _count(db, users.where(User.created_at >= now - WEEK)),
        active_this_week=await _count(db, users.where(User.last_login_at >= now - WEEK)),
        # Gate 3 sends and Gate 4 form submissions, by when they happened.
        applications_sent_this_week=await _count(
            db,
            select(func.count(Audit.id)).where(
                Audit.action.in_(["application_sent", "gate_submit"]),
                Audit.created_at >= now - WEEK,
            ),
        ),
        plans_verified=await _count(
            db, select(func.count(SkillPlan.id)).where(SkillPlan.status == "verified")
        ),
        scout_runs_today=await _count(
            db,
            select(func.count(Audit.id)).where(
                Audit.action == "scout_run", Audit.created_at >= now - DAY
            ),
        ),
        emails_today={kind: int(count) for kind, count in emails.all()},
    )


async def audit_log(
    db: AsyncSession, *, limit: int, action: str | None, user_id: int | None
) -> list[AuditRow]:
    query = select(Audit, User.email).outerjoin(User, User.id == Audit.user_id)
    if action:
        query = query.where(Audit.action == action)
    if user_id is not None:
        query = query.where(Audit.user_id == user_id)
    rows = await db.execute(query.order_by(Audit.created_at.desc(), Audit.id.desc()).limit(limit))
    return [AuditRow(entry, email) for entry, email in rows.all()]
