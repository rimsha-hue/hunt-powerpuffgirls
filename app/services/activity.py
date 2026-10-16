"""Activity screen: the audit timeline and the counters that show the human gates working."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Application, Audit, Claim, Job, SkillPlan

SHIPPED = ("sent", "submitted")
AUTOMATIC = ("verified", "inferred", "blocked")


@dataclass(frozen=True)
class Counters:
    checked_automatically: int  # routed by code without asking the user
    sent_to_you: int  # Needs-you claims, answered or still open
    unverified_shipped: int  # open Needs-you claims in a sent or submitted application
    applications_sent: int
    plans_verified: int
    plans_open: int


@dataclass(frozen=True)
class Asked:
    application_id: int
    job_title: str
    company: str
    questions: int
    created_at: datetime


async def _count(db: AsyncSession, query: object) -> int:
    return int((await db.execute(query)).scalar_one())  # type: ignore[call-overload]


async def counters(db: AsyncSession, user_id: int) -> Counters:
    claims = select(func.count(Claim.id)).where(Claim.user_id == user_id)
    automatic = claims.where(Claim.bucket.in_(AUTOMATIC), Claim.user_decision.is_(None))
    asked = claims.where(or_(Claim.bucket == "needs_you", Claim.user_decision.is_not(None)))
    shipped = (
        claims.join(Application, Application.id == Claim.application_id)
        .where(Application.status.in_(SHIPPED))
        .where(Claim.bucket == "needs_you")
    )
    sent = select(func.count(Application.id)).where(
        Application.user_id == user_id, Application.status.in_(SHIPPED)
    )
    plans = select(func.count(SkillPlan.id)).where(SkillPlan.user_id == user_id)
    return Counters(
        checked_automatically=await _count(db, automatic),
        sent_to_you=await _count(db, asked),
        unverified_shipped=await _count(db, shipped),
        applications_sent=await _count(db, sent),
        plans_verified=await _count(db, plans.where(SkillPlan.status == "verified")),
        plans_open=await _count(db, plans.where(SkillPlan.status != "verified")),
    )


async def questions_by_application(db: AsyncSession, user_id: int, limit: int) -> list[Asked]:
    """Questions asked per application, oldest first; the learning loop should make it fall."""
    rows = await db.execute(
        select(
            Application.id,
            Job.title,
            Job.company,
            Application.questions_asked,
            Application.created_at,
        )
        .join(Job, Job.id == Application.job_id)
        .where(Application.user_id == user_id)
        .order_by(Application.created_at.desc(), Application.id.desc())
        .limit(limit)
    )
    return [Asked(*row) for row in reversed(rows.all())]


async def timeline(db: AsyncSession, user_id: int, limit: int) -> list[Audit]:
    rows = await db.execute(
        select(Audit)
        .where(Audit.user_id == user_id)
        .order_by(Audit.created_at.desc(), Audit.id.desc())
        .limit(limit)
    )
    return list(rows.scalars())
