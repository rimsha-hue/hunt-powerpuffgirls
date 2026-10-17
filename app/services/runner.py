"""Runs the Scout and the Notifier for a user, from "Fetch now", the schedule or the internal
endpoint. One run per user at a time; each run uses its own database session and commits the jobs
before emailing, so a failed email never loses fetched jobs."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.db.models import Job, Profile, User
from app.domain.locations import LocationIndex
from app.domain.status import OPEN_STATUSES
from app.domain.taxonomy import Taxonomy
from app.integrations.email import EmailProvider
from app.integrations.fetch import SafeFetcher
from app.services import liveness
from app.services.notifier import DigestResult, send_digest
from app.services.scout import ScoutReport, ScoutSources, run_scout

logger = structlog.get_logger()


@dataclass
class RunResult:
    report: ScoutReport
    digest: DigestResult


class ScoutRunner:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        sources: ScoutSources,
        email: EmailProvider,
        taxonomy: Taxonomy,
        locations: Callable[[], Awaitable[LocationIndex]],
        settings: Settings,
        fetcher: SafeFetcher,
    ) -> None:
        self.session_factory = session_factory
        self.sources = sources
        self.email = email
        self.taxonomy = taxonomy
        self.locations = locations
        self.settings = settings
        self.fetcher = fetcher
        self._locks: dict[int, asyncio.Lock] = {}

    async def _close_gone_jobs(self, db: AsyncSession, user_id: int, report: ScoutReport) -> None:
        """Asks each open job's own site whether it still takes applications, before the digest
        offers it. Recorded and demo jobs point nowhere real, so they are not asked."""
        if report.used_recorded or self.settings.demo_mode:
            return
        jobs = (
            await db.execute(
                select(Job).where(Job.user_id == user_id, Job.status.in_(sorted(OPEN_STATUSES)))
            )
        ).scalars()
        await liveness.refresh_many(
            db,
            list(jobs),
            self.fetcher,
            now=datetime.now(UTC),
            recheck=timedelta(hours=self.settings.job_open_recheck_hours),
            limit=self.settings.job_open_checks_per_run,
            concurrency=self.settings.job_open_check_concurrency,
        )

    def busy(self, user_id: int) -> bool:
        lock = self._locks.get(user_id)
        return lock is not None and lock.locked()

    async def run_for_user(self, user_id: int) -> RunResult:
        """Raises ScoutError (safe to show) when the user can't be scouted yet."""
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            locations = await self.locations()
            async with self.session_factory() as db:
                user = (
                    await db.execute(
                        select(User).where(User.id == user_id).options(selectinload(User.profile))
                    )
                ).scalar_one()
                report = await run_scout(
                    db,
                    user,
                    sources=self.sources,
                    taxonomy=self.taxonomy,
                    locations=locations,
                    max_queries=self.settings.scout_max_queries,
                    max_age_days=self.settings.scout_max_age_days,
                    demo_mode=self.settings.demo_mode,
                    now=datetime.now(UTC),
                )
                await db.commit()
                await self._close_gone_jobs(db, user_id, report)
                await db.commit()
                digest = await send_digest(
                    db,
                    user,
                    email=self.email,
                    base_url=self.settings.base_url,
                    link_hours=self.settings.apply_link_hours,
                    now=datetime.now(UTC),
                )
                await db.commit()
        logger.info(
            "scout_finished",
            user_id=user_id,
            new=len(report.new_job_ids),
            digest_jobs=digest.sent,
        )
        return RunResult(report, digest)

    async def run_all(self) -> int:
        """Scouts every user who has target roles; one user's failure doesn't stop the rest."""
        async with self.session_factory() as db:
            rows = await db.execute(select(Profile.user_id, Profile.target_roles))
            user_ids = [user_id for user_id, roles in rows if roles]
        done = 0
        for user_id in user_ids:
            try:
                await self.run_for_user(user_id)
                done += 1
            except Exception as exc:  # one user's failure must not stop the others
                logger.warning("scout_user_failed", user_id=user_id, error_type=type(exc).__name__)
        logger.info("scout_all_finished", users=len(user_ids), succeeded=done)
        return done


class ScoutScheduler:
    """APScheduler in this process: runs every user's Scout every SCOUT_INTERVAL_HOURS. The first
    run waits a full interval, so restarts (and dev reloads) don't spend the job APIs' quotas."""

    def __init__(self, runner: ScoutRunner, interval_hours: int) -> None:
        self.runner = runner
        self.interval_hours = interval_hours
        self._scheduler: AsyncIOScheduler | None = None

    @property
    def running(self) -> bool:
        return self._scheduler is not None and bool(self._scheduler.running)

    def start(self) -> None:
        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(
            self.runner.run_all,
            "interval",
            hours=self.interval_hours,
            id="scout",
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        self._scheduler = scheduler
        logger.info("scheduler_started", interval_hours=self.interval_hours)

    def shutdown(self) -> None:
        if self._scheduler is not None and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        self._scheduler = None
