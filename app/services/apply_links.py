"""Signed Apply links (spec section 13.3): random 32-byte tokens stored only as hashes, single use,
expiring, and bound to one user and one job."""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApplyToken
from app.services.auth import hash_token


class ApplyLinkError(Exception):
    """Safe to show to the user."""


def _aware(moment: datetime) -> datetime:
    # SQLite returns naive datetimes; every stored time is UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def issue(
    db: AsyncSession, *, user_id: int, job_id: int, hours: int, now: datetime
) -> tuple[str, ApplyToken]:
    """Stores a new token's hash; returns the raw token for the link and the stored row."""
    token = secrets.token_urlsafe(32)
    row = ApplyToken(
        user_id=user_id,
        job_id=job_id,
        token_hash=hash_token(token),
        created_at=now,
        expires_at=now + timedelta(hours=hours),
    )
    db.add(row)
    return token, row


def link(base_url: str, job_id: int, token: str) -> str:
    return f"{base_url}/apply/{job_id}?t={token}"


async def redeem(db: AsyncSession, *, job_id: int, token: str, now: datetime) -> ApplyToken:
    """Marks the token used and returns it, or raises ApplyLinkError."""
    result = await db.execute(select(ApplyToken).where(ApplyToken.token_hash == hash_token(token)))
    row = result.scalar_one_or_none()
    # The same message for unknown tokens and tokens for another job, so links can't be probed.
    if row is None or row.job_id != job_id:
        raise ApplyLinkError("This Apply link isn't valid. Log in to see your jobs.")
    if row.used_at is not None:
        raise ApplyLinkError("This Apply link was already used. Log in to continue.")
    if _aware(row.expires_at) <= now:
        raise ApplyLinkError("This Apply link has expired. Log in to see your jobs.")
    row.used_at = now
    return row
