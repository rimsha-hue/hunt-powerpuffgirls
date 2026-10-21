"""The user's data rights: a copy of everything stored about the account (export), and automatic
clean-up of data that is no longer needed (retention).

The export holds every row the account owns, column by column, except secrets: password and
token hashes, session and CSRF tokens, the Google account id, and the bytes of uploaded files
(listed by name and size instead). It is generated from the models, so a new table or column is
exported without anyone remembering to add it.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, inspect, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Application,
    ApplicationFile,
    ApplyToken,
    Audit,
    AuthSession,
    Base,
    Claim,
    ClaimBlocklist,
    EmailLog,
    EmailToken,
    Evidence,
    Job,
    Notification,
    Profile,
    ResumeLine,
    SkillPlan,
    User,
)

FORMAT_VERSION = 1
SECRET_COLUMNS = frozenset({"password_hash", "google_sub", "token_hash", "csrf_token", "data"})
# (section name, model): every table that holds rows owned by an account.
SECTIONS: tuple[tuple[str, type[Base]], ...] = (
    ("profile", Profile),
    ("resume_lines", ResumeLine),
    ("evidence", Evidence),
    ("blocked_values", ClaimBlocklist),
    ("jobs", Job),
    ("digests", Notification),
    ("applications", Application),
    ("claims", Claim),
    ("growth_plans", SkillPlan),
    ("activity", Audit),
    ("emails_sent", EmailLog),
)


def _value(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes):
        return None
    return value


def _row(obj: Base) -> dict[str, Any]:
    return {
        attr.key: _value(getattr(obj, attr.key))
        for attr in inspect(obj).mapper.column_attrs
        if attr.key not in SECRET_COLUMNS
    }


async def export(db: AsyncSession, user: User, now: datetime) -> dict[str, Any]:
    data: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "exported_at": now.isoformat(),
        "account": _row(user),
    }
    for name, model in SECTIONS:
        owner = model.user_id  # type: ignore[attr-defined]
        order = inspect(model).primary_key  # profiles are keyed by user_id, not id
        rows = await db.execute(select(model).where(owner == user.id).order_by(*order))
        data[name] = [_row(row) for row in rows.scalars()]
    files = await db.execute(
        select(
            ApplicationFile.id,
            ApplicationFile.application_id,
            ApplicationFile.kind,
            ApplicationFile.filename,
            ApplicationFile.content_type,
            func.length(ApplicationFile.data),
        ).where(ApplicationFile.user_id == user.id)
    )
    data["uploaded_files"] = [
        {
            "id": file_id,
            "application_id": application_id,
            "kind": kind,
            "filename": filename,
            "content_type": content_type,
            "bytes": int(size or 0),
        }
        for file_id, application_id, kind, filename, content_type, size in files.all()
    ]
    return data


@dataclass(frozen=True)
class Cleanup:
    sessions: int
    links: int
    email_log: int
    unverified_accounts: int


async def clean(
    db: AsyncSession,
    *,
    now: datetime,
    unverified_days: int,
    email_log_days: int,
    token_days: int,
    keep_emails: frozenset[str],
) -> Cleanup:
    """Removes what is no longer needed. An unverified account goes only when it was never
    verified and nobody signed in to it for `unverified_days`; admins and `keep_emails` (the
    demo account) are never removed."""
    sessions = await db.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    link_cutoff = now - timedelta(days=token_days)
    links = 0
    for model in (EmailToken, ApplyToken):
        done = await db.execute(
            delete(model).where(
                model.created_at < link_cutoff,
                or_(model.used_at.is_not(None), model.expires_at <= now),
            )
        )
        links += done.rowcount or 0  # type: ignore[attr-defined]
    log = await db.execute(
        delete(EmailLog).where(EmailLog.created_at < now - timedelta(days=email_log_days))
    )
    account_cutoff = now - timedelta(days=unverified_days)
    stale = and_(
        User.email_verified_at.is_(None),
        User.created_at < account_cutoff,
        or_(User.last_login_at.is_(None), User.last_login_at < account_cutoff),
        User.role != "admin",
    )
    if keep_emails:
        stale = and_(stale, User.email.not_in(list(keep_emails)))
    # ON DELETE CASCADE removes everything the account owns.
    accounts = await db.execute(delete(User).where(stale))
    return Cleanup(
        sessions=sessions.rowcount or 0,  # type: ignore[attr-defined]
        links=links,
        email_log=log.rowcount or 0,  # type: ignore[attr-defined]
        unverified_accounts=accounts.rowcount or 0,  # type: ignore[attr-defined]
    )
