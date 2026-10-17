"""The applications pipeline: every job the user picked, at its stage, with follow-ups due; and
jobs the user applied to on their own, tracked here alongside the rest."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import and_, select

from app.api.deps import CurrentUser, Db
from app.api.schemas import JobOut, PipelineItemOut
from app.db.models import Application, Job
from app.domain.status import InvalidTransitionError, applied_elsewhere, pipeline_stage
from app.services import audit

router = APIRouter(prefix="/api", tags=["pipeline"])

# Jobs never picked, or dismissed, are the Jobs page's business, not the pipeline's.
NOT_IN_PIPELINE = ("new", "skipped")
WENT_OUT = ("sent", "submitted")  # application statuses once Power Hunt sent it
AFTER_APPLYING = ("applied", "interview", "assessment", "offer", "rejected")


@router.get("/applications")
async def list_pipeline(principal: CurrentUser, db: Db) -> list[PipelineItemOut]:
    now = datetime.now(UTC)
    rows = await db.execute(
        select(Job, Application)
        .outerjoin(
            Application,
            and_(Application.job_id == Job.id, Application.user_id == Job.user_id),
        )
        .where(Job.user_id == principal.user.id, Job.status.not_in(NOT_IN_PIPELINE))
        .order_by(Job.status_changed_at.desc(), Job.id.desc())
    )
    items: list[PipelineItemOut] = []
    for job, application in rows.tuples():
        app_status = application.status if application else None
        follow_up = application.follow_up_due if application else None
        stage = pipeline_stage(job.status, app_status, follow_up, now)
        if stage == "closed" and app_status is None:
            continue  # closed before the user did anything: nothing to track
        items.append(
            PipelineItemOut(
                job=JobOut.model_validate(job),
                stage=stage,
                application_status=app_status,
                sent_at=application.sent_at if application else None,
                follow_up_due=follow_up,
                ats_after=application.ats_after if application else None,
                external=job.status in AFTER_APPLYING and app_status not in WENT_OUT,
            )
        )
    return items


@router.post("/jobs/{job_id}/applied-elsewhere")
async def mark_applied_elsewhere(job_id: int, principal: CurrentUser, db: Db) -> JobOut:
    """The user applied on the employer's site themselves; Power Hunt tracks the outcome."""
    job = await db.get(Job, job_id)
    if job is None or job.user_id != principal.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")
    try:
        job.status = applied_elsewhere(job.status)
    except InvalidTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    job.status_changed_at = datetime.now(UTC)
    audit.record(
        db,
        user_id=principal.user.id,
        actor="user",
        action="applied_elsewhere",
        entity="job",
        entity_id=job.id,
    )
    return JobOut.model_validate(job)
