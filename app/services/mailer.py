"""The email log and the per-account daily limit.

Every email sent for a user is logged with its kind and time only (never its content). Digests
and applications, which a user can trigger in volume, are capped per rolling 24 hours, so a
compromised or abusive account can't turn the sending mailbox into a spam relay.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EmailLog

# Kinds a user can send in volume; transactional mail (verify, reset, alerts) isn't capped here,
# because the endpoints that trigger it are rate limited per address instead.
LIMITED_KINDS = frozenset({"digest", "application"})
WINDOW = timedelta(hours=24)


class SendLimitError(Exception):
    """Safe to show to the user."""


def record(db: AsyncSession, user_id: int, kind: str, now: datetime) -> None:
    db.add(EmailLog(user_id=user_id, kind=kind, created_at=now))


async def sent_since(
    db: AsyncSession, user_id: int, since: datetime, kinds: Iterable[str] = LIMITED_KINDS
) -> int:
    count = await db.scalar(
        select(func.count(EmailLog.id)).where(
            EmailLog.user_id == user_id,
            EmailLog.kind.in_(list(kinds)),
            EmailLog.created_at >= since,
        )
    )
    return int(count or 0)


async def check_limit(db: AsyncSession, user_id: int, *, limit: int, now: datetime) -> None:
    """Raises SendLimitError when the account already sent `limit` digests and applications in
    the last 24 hours."""
    if await sent_since(db, user_id, now - WINDOW) >= limit:
        raise SendLimitError(
            f"This account reached its limit of {limit} emails in 24 hours. Try again tomorrow."
        )
