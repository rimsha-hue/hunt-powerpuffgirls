"""Keeps closed postings out of the user's way: a job whose source date has passed, or whose own
site says it is gone, moves to expired (shown as Closed)."""

from collections.abc import Sequence
from datetime import datetime, timedelta

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job
from app.domain.status import OPEN_STATUSES, transition
from app.integrations.fetch import SafeFetcher
from app.integrations.liveness import Liveness, check_posting
from app.services import audit


def _past_expiry(job: Job, now: datetime) -> bool:
    return job.expires_at is not None and job.expires_at <= now


def _due(job: Job, now: datetime, recheck: timedelta) -> bool:
    """Open, and either past its date or not confirmed open within `recheck`."""
    return job.status in OPEN_STATUSES and (
        _past_expiry(job, now)
        or job.open_checked_at is None
        or now - job.open_checked_at >= recheck
    )


def _apply(db: AsyncSession, job: Job, now: datetime, site: Liveness | None) -> None:
    """A site that couldn't be read ("unknown") leaves the job as it is, to be asked again."""
    if job.status not in OPEN_STATUSES:
        return
    reason = "expiry_date" if _past_expiry(job, now) else "site_says_closed"
    if _past_expiry(job, now) or site == "closed":
        job.status = transition(job.status, "expired")
        job.status_changed_at = now
        audit.record(
            db,
            user_id=job.user_id,
            actor="scout",
            action="job_closed",
            entity="job",
            entity_id=job.id,
            reason=reason,
        )
    elif site == "open":
        job.open_checked_at = now


async def refresh(
    db: AsyncSession, job: Job, fetcher: SafeFetcher, *, now: datetime, recheck: timedelta
) -> None:
    """Asks the posting's site again if it is due, and closes the job if it is gone."""
    await refresh_many(db, [job], fetcher, now=now, recheck=recheck, limit=1, concurrency=1)


async def refresh_many(
    db: AsyncSession,
    jobs: Sequence[Job],
    fetcher: SafeFetcher,
    *,
    now: datetime,
    recheck: timedelta,
    limit: int,
    concurrency: int,
) -> None:
    """Checks up to `limit` due jobs, best match first, `concurrency` sites at a time. The
    session is touched only after every site has answered."""
    due = sorted((j for j in jobs if _due(j, now, recheck)), key=lambda j: -j.match_score)[:limit]
    sites: list[Liveness | None] = [None] * len(due)
    gate = anyio.Semaphore(concurrency)

    async def ask(index: int, job: Job) -> None:
        async with gate:
            sites[index] = await check_posting(job.apply_url or job.url, fetcher)

    async with anyio.create_task_group() as group:
        for index, job in enumerate(due):
            if not _past_expiry(job, now):
                group.start_soon(ask, index, job)
    for job, site in zip(due, sites, strict=True):
        _apply(db, job, now, site)
