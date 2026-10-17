"""Jobs: the list and one job, "Fetch now", status changes, Gate 1 (Apply now), the signed Apply
link from the digest email, and the protected internal Scout trigger."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.auth import set_session_cookie
from app.api.deps import (
    SESSION_COOKIE,
    AnyPrincipal,
    AppSettings,
    CurrentUser,
    Db,
    Fetcher,
    Locations,
    Principal,
    Runner,
    Tax,
    can_open_job,
)
from app.api.ratelimit import rate_limit
from app.api.schemas import (
    JobDetailOut,
    JobImportIn,
    JobOut,
    JobStatusIn,
    KeywordOut,
    ScoutRunOut,
    SourceOutcomeOut,
)
from app.db.models import Job, User
from app.domain.ats import Keyword
from app.domain.status import OPEN_STATUSES, InvalidTransitionError, transition
from app.integrations.jobs.base import JobSourceError
from app.integrations.jobs.greenhouse import NotGreenhouseError, greenhouse_posting
from app.services import audit, liveness
from app.services.apply_links import ApplyLinkError, redeem
from app.services.auth import create_session, resolve_session, tokens_match
from app.services.scout import ScoutError, import_posting

router = APIRouter(tags=["jobs"])

# A session opened from an Apply link only needs to last while the user works on that job.
SCOPED_SESSION_DAYS = 1


async def _job_for(db: Db, principal: Principal, job_id: int) -> Job:
    job = await db.get(Job, job_id)
    # One 404 for "missing", "someone else's" and "outside this link's scope".
    if job is None or not can_open_job(principal, job.user_id, job.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")
    return job


def _detail(job: Job) -> JobDetailOut:
    found = set(job.ats_detail_json.get("matched", []))
    keywords = [Keyword.from_json(k) for k in job.ats_keywords_json]
    return JobDetailOut(
        **JobOut.model_validate(job).model_dump(),
        description=job.description,
        keywords=[
            KeywordOut(term=k.term, kind=k.kind, weight=k.weight, found=k.term in found)
            for k in keywords
        ],
        ats_checks=job.ats_detail_json.get("checks", {}),
        ats_coverage=float(job.ats_detail_json.get("coverage", 0.0)),
    )


@router.get("/api/jobs")
async def list_jobs(
    principal: CurrentUser,
    db: Db,
    view: Annotated[Literal["open", "all", "skipped", "applied", "closed"], Query()] = "open",
    method: Annotated[Literal["email", "form", "unknown"] | None, Query()] = None,
    min_match: Annotated[int, Query(ge=0, le=100)] = 0,
    posted_from: Annotated[date | None, Query()] = None,
    posted_to: Annotated[date | None, Query()] = None,
) -> list[JobOut]:
    """`posted_from` and `posted_to` are inclusive days (UTC). A job whose source gives no date
    is left out of a date-filtered list, since it can't be shown to be in range."""
    if posted_from and posted_to and posted_from > posted_to:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The start date is after the end date."
        )
    stmt = select(Job).where(Job.user_id == principal.user.id, Job.match_score >= min_match)
    if view == "open":
        stmt = stmt.where(Job.status.in_(OPEN_STATUSES))
    elif view == "skipped":
        stmt = stmt.where(Job.status == "skipped")
    elif view == "closed":
        stmt = stmt.where(Job.status == "expired")
    elif view == "applied":
        stmt = stmt.where(Job.status.not_in([*OPEN_STATUSES, "skipped", "expired"]))
    if method:
        stmt = stmt.where(Job.apply_method == method)
    if posted_from:
        stmt = stmt.where(Job.posted_at >= datetime.combine(posted_from, time.min, UTC))
    if posted_to:
        stmt = stmt.where(
            Job.posted_at < datetime.combine(posted_to + timedelta(days=1), time.min, UTC)
        )
    stmt = stmt.order_by(Job.match_score.desc(), Job.fetched_at.desc(), Job.id.desc())
    return [JobOut.model_validate(job) for job in (await db.execute(stmt)).scalars()]


@router.get("/api/jobs/{job_id}")
async def get_job(
    job_id: int, principal: AnyPrincipal, db: Db, fetcher: Fetcher, settings: AppSettings
) -> JobDetailOut:
    """Opening a job asks its site again (when due) whether it still takes applications."""
    job = await _job_for(db, principal, job_id)
    if not settings.demo_mode:
        await liveness.refresh(
            db,
            job,
            fetcher,
            now=datetime.now(UTC),
            recheck=timedelta(hours=settings.job_open_recheck_hours),
        )
    return _detail(job)


@router.post(
    "/api/jobs/import",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("ingest"))],
)
async def import_job(
    body: JobImportIn,
    request: Request,
    principal: CurrentUser,
    db: Db,
    taxonomy: Tax,
    locations: Locations,
) -> JobOut:
    """Add a job by its link (Greenhouse), scored like the Scout's finds."""
    try:
        found = await greenhouse_posting(request.app.state.http, str(body.url))
    except NotGreenhouseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except JobSourceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    job, _created = await import_posting(
        db, principal.user, found, taxonomy=taxonomy, locations=locations, now=datetime.now(UTC)
    )
    return JobOut.model_validate(job)


@router.post("/api/jobs/fetch", dependencies=[Depends(rate_limit("scout"))])
async def fetch_now(principal: CurrentUser, runner: Runner) -> ScoutRunOut:
    if runner.busy(principal.user.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "A fetch is already running. Hang on.")
    try:
        result = await runner.run_for_user(principal.user.id)
    except ScoutError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    report = result.report
    return ScoutRunOut(
        queries=report.queries,
        fetched=report.fetched,
        relevant=report.relevant,
        duplicates=report.duplicates,
        new_jobs=len(report.new_job_ids),
        rescored=report.rescored,
        used_recorded=report.used_recorded,
        sources=[
            SourceOutcomeOut(name=o.name, fetched=o.fetched, error=o.error) for o in report.sources
        ],
        digest_sent=result.digest.sent,
        digest_note=result.digest.note,
    )


def _move(db: Db, job: Job, target: str, *, user_id: int, actor: str, action: str) -> None:
    try:
        job.status = transition(job.status, target)
    except InvalidTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    job.status_changed_at = datetime.now(UTC)
    audit.record(
        db, user_id=user_id, actor=actor, action=action, entity="job", entity_id=job.id, to=target
    )


@router.post("/api/jobs/{job_id}/status")
async def set_status(job_id: int, body: JobStatusIn, principal: CurrentUser, db: Db) -> JobOut:
    job = await _job_for(db, principal, job_id)
    _move(db, job, body.status, user_id=principal.user.id, actor="user", action="job_status")
    return JobOut.model_validate(job)


@router.post("/api/jobs/{job_id}/apply")
async def apply_now(job_id: int, principal: AnyPrincipal, db: Db) -> JobOut:
    """Gate 1 (Choose): the user picks this job. Choosing it again changes nothing."""
    job = await _job_for(db, principal, job_id)
    if job.status == "new":
        _move(db, job, "shortlisted", user_id=principal.user.id, actor="user", action="gate_choose")
    return JobOut.model_validate(job)


@router.get("/apply/{job_id}", include_in_schema=False, dependencies=[Depends(rate_limit("login"))])
async def open_apply_link(
    job_id: int,
    request: Request,
    db: Db,
    settings: AppSettings,
    t: Annotated[str, Query(min_length=20, max_length=100)],
) -> RedirectResponse:
    """The Apply now button in the digest. A valid link counts as Gate 1 and opens the job; with no
    session it signs the user in for this job only."""
    now = datetime.now(UTC)
    try:
        token = await redeem(db, job_id=job_id, token=t, now=now)
    except ApplyLinkError:
        return RedirectResponse("/login?error=apply_link", status_code=status.HTTP_302_FOUND)
    job = await db.get(Job, job_id)
    if job is None or job.user_id != token.user_id:
        return RedirectResponse("/login?error=apply_link", status_code=status.HTTP_302_FOUND)

    response = RedirectResponse(f"/jobs/{job_id}", status_code=status.HTTP_302_FOUND)
    cookie = request.cookies.get(SESSION_COOKIE)
    current = await resolve_session(db, cookie) if cookie else None
    if current is None or current[1].id != token.user_id:
        user = await db.get(User, token.user_id)
        assert user is not None  # the token's user owns the job, checked above
        raw, _session = await create_session(
            db, user, days=SCOPED_SESSION_DAYS, scope_job_id=job_id
        )
        set_session_cookie(response, raw, settings)
    if job.status == "new":
        _move(db, job, "shortlisted", user_id=token.user_id, actor="user", action="gate_choose")
    audit.record(
        db,
        user_id=token.user_id,
        actor="user",
        action="apply_link_used",
        entity="job",
        entity_id=job_id,
    )
    return response


@router.post("/internal/run-scout", include_in_schema=False)
async def run_scout_for_everyone(
    settings: AppSettings,
    runner: Runner,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, int]:
    """Manual or cron trigger for the scheduled Scout. Disabled unless INTERNAL_TOKEN is set."""
    expected = settings.internal_token.get_secret_value() if settings.internal_token else ""
    if not expected or not tokens_match(expected, x_internal_token):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    return {"users": await runner.run_all()}
