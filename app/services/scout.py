"""Scout, Matcher and Classifier: fetch jobs for a user's roles and places, keep the relevant new
ones, and score each (match, ATS before) and classify how to apply.

Sources are tried in tiers: JSearch, then Remotive and Arbeitnow, and recorded postings only when
every live source failed (or in demo mode). A tier that finds relevant jobs ends the search.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Evidence, Job, ResumeLine, User
from app.domain.ats import CvDocument, Keyword, cv_from_lines, extract_keywords, score_cv
from app.domain.classify import classify
from app.domain.jobs import (
    RELEVANCE_THRESHOLD,
    JobPosting,
    best_role_relevance,
    dedupe_hash,
    location_fit,
)
from app.domain.locations import REMOTE, LocationIndex, Place
from app.domain.matching import match_job
from app.domain.status import OPEN_STATUSES
from app.domain.taxonomy import Taxonomy
from app.integrations.jobs.base import JobQuery, JobSource, JobSourceError
from app.services import audit

logger = structlog.get_logger()


class ScoutError(Exception):
    """Safe to show to the user."""


@dataclass
class SourceOutcome:
    name: str
    fetched: int = 0
    error: str | None = None


@dataclass
class ScoutReport:
    queries: int
    fetched: int = 0
    relevant: int = 0
    duplicates: int = 0
    new_job_ids: list[int] = field(default_factory=list)
    sources: list[SourceOutcome] = field(default_factory=list)
    used_recorded: bool = False
    rescored: int = 0


@dataclass(frozen=True)
class ScoutSources:
    primary: Sequence[JobSource]
    secondary: Sequence[JobSource]
    recorded: JobSource


def target_places(keys: Sequence[str], locations: LocationIndex) -> list[Place]:
    places: list[Place] = []
    for key in keys:
        place = REMOTE if key == "remote" else locations.get(key)
        if place is not None and place not in places:
            places.append(place)
    return places


def build_queries(roles: Sequence[str], places: Sequence[Place], limit: int) -> list[JobQuery]:
    """Up to `limit` role x place searches, walked diagonally so a small budget still covers
    every role and every place before repeating either."""
    options: list[Place | None] = list(places) or [None]
    pairs: list[JobQuery] = []
    total = len(roles) * len(options)
    step = 0
    while len(pairs) < min(limit, total):
        role = roles[step % len(roles)]
        place = options[(step // len(roles) + step) % len(options)]
        query = JobQuery(role, place)
        if query not in pairs:
            pairs.append(query)
        step += 1
        if step > total * total:  # every pair has been visited
            break
    return pairs


def _is_fresh(posting: JobPosting, live: bool, cutoff: datetime) -> bool:
    return not live or posting.posted_at is None or posting.posted_at >= cutoff


async def _collect(
    tier: Sequence[JobSource],
    queries: Sequence[JobQuery],
    report: ScoutReport,
) -> list[tuple[JobPosting, bool]]:
    found: list[tuple[JobPosting, bool]] = []
    for source in tier:
        outcome = SourceOutcome(source.name)
        report.sources.append(outcome)
        try:
            postings = await source.search(queries)
        except JobSourceError as exc:
            outcome.error = str(exc)
            logger.warning("scout_source_failed", source=source.name)
            continue
        outcome.fetched = len(postings)
        report.fetched += len(postings)
        found.extend((p, source.live) for p in postings)
    return found


async def scoring_context(db: AsyncSession, user_id: int) -> tuple[set[str], CvDocument]:
    """The user's proven skills and their resume as a CV document, for scoring jobs."""
    ledger_skills = set(
        (
            await db.execute(
                select(Evidence.normalized_value).where(
                    Evidence.user_id == user_id, Evidence.type == "skill"
                )
            )
        ).scalars()
    )
    resume = await db.execute(
        select(ResumeLine.section, ResumeLine.text)
        .where(ResumeLine.user_id == user_id)
        .order_by(ResumeLine.line_no)
    )
    return ledger_skills, cv_from_lines((section, text) for section, text in resume)


def build_job(
    user_id: int,
    posting: JobPosting,
    digest: str,
    *,
    relevance: float,
    places: Sequence[Place],
    ledger_skills: set[str],
    cv: CvDocument,
    taxonomy: Taxonomy,
    now: datetime,
) -> Job:
    """A scored, classified job: frozen ATS keywords and "before" score, match, apply method."""
    keywords = extract_keywords(posting.description, taxonomy, company=posting.company)
    ats = score_cv(keywords, cv, taxonomy)
    fit = match_job(keywords, ledger_skills, relevance, location_fit(posting, places))
    how = classify(posting.description, posting.apply_url or posting.url)
    return Job(
        user_id=user_id,
        source=posting.source,
        external_id=posting.external_id,
        dedupe_hash=digest,
        title=posting.title,
        company=posting.company,
        location=posting.location,
        remote=posting.remote,
        url=posting.url,
        description=posting.description,
        posted_at=posting.posted_at,
        fetched_at=now,
        apply_method=how.method,
        apply_email=how.apply_email,
        apply_url=how.apply_url,
        required_attachments=list(how.attachments),
        ats_keywords_json=[k.to_json() for k in keywords],
        ats_before=ats.score,
        ats_detail_json=ats.detail_json(),
        match_score=fit.score,
        matched_skills=fit.matched_skills,
        missing_skills=fit.missing_skills,
        status="new",
        status_changed_at=now,
    )


def _as_posting(job: Job) -> JobPosting:
    return JobPosting(
        source=job.source,
        external_id=job.external_id,
        title=job.title,
        company=job.company,
        location=job.location,
        remote=job.remote,
        url=job.url,
        description="",
        posted_at=job.posted_at,
    )


async def _rescore_open_jobs(
    db: AsyncSession,
    user_id: int,
    roles: Sequence[str],
    places: Sequence[Place],
    ledger_skills: set[str],
) -> int:
    """The Matcher re-scores open jobs against today's ledger and targets, so a newly proven
    skill raises the jobs that need it. ATS keywords and "before" scores stay frozen."""
    result = await db.execute(
        select(Job).where(Job.user_id == user_id, Job.status.in_(OPEN_STATUSES))
    )
    changed = 0
    for job in result.scalars():
        keywords = [Keyword.from_json(k) for k in job.ats_keywords_json]
        fit = match_job(
            keywords,
            ledger_skills,
            best_role_relevance(roles, job.title),
            location_fit(_as_posting(job), places),
        )
        if (fit.score, fit.matched_skills, fit.missing_skills) != (
            job.match_score,
            job.matched_skills,
            job.missing_skills,
        ):
            job.match_score = fit.score
            job.matched_skills = fit.matched_skills
            job.missing_skills = fit.missing_skills
            changed += 1
    return changed


async def run_scout(
    db: AsyncSession,
    user: User,
    *,
    sources: ScoutSources,
    taxonomy: Taxonomy,
    locations: LocationIndex,
    max_queries: int,
    max_age_days: int,
    demo_mode: bool,
    now: datetime,
) -> ScoutReport:
    profile = user.profile
    roles = [r for r in profile.target_roles if r.strip()]
    if not roles:
        raise ScoutError("Add at least one target role in Onboarding first.")
    places = target_places(profile.target_locations, locations)
    queries = build_queries(roles, places, max_queries)
    report = ScoutReport(queries=len(queries))
    cutoff = now - timedelta(days=max_age_days)

    def relevant(found: list[tuple[JobPosting, bool]]) -> list[tuple[JobPosting, float]]:
        kept: list[tuple[JobPosting, float]] = []
        for posting, live in found:
            score = best_role_relevance(roles, posting.title)
            if (
                score >= RELEVANCE_THRESHOLD
                and location_fit(posting, places)
                and _is_fresh(posting, live, cutoff)
            ):
                kept.append((posting, score))
        return kept

    tiers = [] if demo_mode else [t for t in (sources.primary, sources.secondary) if t]
    candidates: list[tuple[JobPosting, float]] = []
    for tier in tiers:
        candidates = relevant(await _collect(tier, queries, report))
        if candidates:
            break
    every_live_source_failed = all(o.error for o in report.sources)
    if not candidates and every_live_source_failed:
        report.used_recorded = True
        candidates = relevant(await _collect([sources.recorded], queries, report))
    report.relevant = len(candidates)

    existing = await db.execute(
        select(Job.source, Job.external_id, Job.dedupe_hash).where(Job.user_id == user.id)
    )
    seen_ids: set[tuple[str, str]] = set()
    seen_hashes: set[str] = set()
    for source_name, external_id, digest in existing:
        seen_ids.add((source_name, external_id))
        seen_hashes.add(digest)

    ledger_skills, cv = await scoring_context(db, user.id)

    report.rescored = await _rescore_open_jobs(db, user.id, roles, places, ledger_skills)

    new_jobs: list[Job] = []
    for posting, relevance in sorted(candidates, key=lambda c: -c[1]):
        digest = dedupe_hash(posting.company, posting.title, posting.location)
        if (posting.source, posting.external_id) in seen_ids or digest in seen_hashes:
            report.duplicates += 1
            continue
        seen_ids.add((posting.source, posting.external_id))
        seen_hashes.add(digest)

        new_jobs.append(
            build_job(
                user.id,
                posting,
                digest,
                relevance=relevance,
                places=places,
                ledger_skills=ledger_skills,
                cv=cv,
                taxonomy=taxonomy,
                now=now,
            )
        )
    db.add_all(new_jobs)
    await db.flush()
    report.new_job_ids = [job.id for job in new_jobs]

    audit.record(
        db,
        user_id=user.id,
        actor="scout",
        action="scout_run",
        entity="jobs",
        queries=report.queries,
        fetched=report.fetched,
        relevant=report.relevant,
        new=len(new_jobs),
        duplicates=report.duplicates,
        recorded=report.used_recorded,
        failed_sources=[o.name for o in report.sources if o.error],
    )
    return report


async def import_posting(
    db: AsyncSession,
    user: User,
    posting: JobPosting,
    *,
    taxonomy: Taxonomy,
    locations: LocationIndex,
    now: datetime,
) -> tuple[Job, bool]:
    """A posting the user found themselves (Gate 1 already: they chose it). Returns (job,
    created); an existing copy of the same posting is returned instead of a duplicate."""
    digest = dedupe_hash(posting.company, posting.title, posting.location)
    existing = (
        await db.execute(
            select(Job).where(
                Job.user_id == user.id,
                (Job.dedupe_hash == digest)
                | ((Job.source == posting.source) & (Job.external_id == posting.external_id)),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    places = target_places(user.profile.target_locations, locations)
    ledger_skills, cv = await scoring_context(db, user.id)
    job = build_job(
        user.id,
        posting,
        digest,
        relevance=best_role_relevance(user.profile.target_roles, posting.title),
        places=places,
        ledger_skills=ledger_skills,
        cv=cv,
        taxonomy=taxonomy,
        now=now,
    )
    db.add(job)
    await db.flush()
    audit.record(
        db,
        user_id=user.id,
        actor="user",
        action="job_added_by_link",
        entity="job",
        entity_id=job.id,
    )
    return job, True
