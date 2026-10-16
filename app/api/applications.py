"""Apply Workspace: tailor and verify the CV for a job, answer Needs-you claims (Gate 2), approve
the CV and download it as a PDF. An Apply-link session may work on its own job here."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import select

from app.api.deps import LLM, AnyPrincipal, AppSettings, Db, Principal, Tax, can_open_job
from app.api.ledger import evidence_out
from app.api.ratelimit import rate_limit
from app.api.schemas import (
    ApplicationOut,
    AtsAfterOut,
    ClaimDecisionIn,
    ClaimOut,
    ClaimSourceOut,
    CvBlockOut,
)
from app.db.models import Application, Claim, Evidence, Job, ResumeLine
from app.integrations.embeddings import Similarity
from app.integrations.pdf import render_cv
from app.services.applications import WorkspaceError, approve_cv, blocks_of, decide, prepare
from app.services.verifier import DecisionError

router = APIRouter(prefix="/api", tags=["workspace"])


def get_similarity(request: Request) -> Similarity:
    similarity: Similarity = request.app.state.similarity
    return similarity


Sim = Annotated[Similarity, Depends(get_similarity)]


async def _job(db: Db, principal: Principal, job_id: int) -> Job:
    job = await db.get(Job, job_id)
    if job is None or not can_open_job(principal, job.user_id, job.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")
    return job


async def _application(db: Db, job: Job) -> Application:
    application = (
        await db.execute(select(Application).where(Application.job_id == job.id))
    ).scalar_one_or_none()
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No tailored CV for this job yet.")
    return application


async def _sources(db: Db, user_id: int, claims: list[Claim]) -> dict[int, ClaimSourceOut]:
    """Where each Verified or Inferred claim's support comes from, for the badge tooltip."""
    evidence = list(
        (await db.execute(select(Evidence).where(Evidence.user_id == user_id))).scalars()
    )
    described = {e.id: e for e in await evidence_out(db, evidence)}
    by_value: dict[tuple[str, str], int] = {}
    metric_keys: dict[str, int] = {}
    for e in evidence:
        by_value.setdefault((e.type, e.normalized_value), e.id)
        if e.type == "metric":
            for key in e.normalized_value.split("|"):
                metric_keys.setdefault(key, e.id)
    line_ids = {c.cited_line_id for c in claims if c.cited_line_id}
    lines = (
        {
            line.id: line
            for line in (
                await db.execute(select(ResumeLine).where(ResumeLine.id.in_(line_ids)))
            ).scalars()
        }
        if line_ids
        else {}
    )

    out: dict[int, ClaimSourceOut] = {}
    for claim in claims:
        if claim.bucket == "verified":
            evidence_id = claim.evidence_id or by_value.get((claim.type, claim.normalized_value))
            if evidence_id is None and claim.type == "metric":
                evidence_id = metric_keys.get(claim.normalized_value.split("|")[0])
            item = described.get(evidence_id) if evidence_id else None
            if item:
                out[claim.id] = ClaimSourceOut(
                    label=item.source_label, detail=item.source_detail, url=item.source_url
                )
        elif claim.bucket == "inferred" and claim.cited_line_id in lines:
            line = lines[claim.cited_line_id]
            similarity = (
                f", {round((claim.similarity or 0) * 100)}% similar" if claim.similarity else ""
            )
            out[claim.id] = ClaimSourceOut(
                label="Your resume line",
                detail=f"Line {line.line_no} ({line.section}{similarity}): {line.text}",
                url=None,
            )
    return out


async def _out(db: Db, application: Application, job: Job) -> ApplicationOut:
    claims = list(
        (
            await db.execute(
                select(Claim)
                .where(Claim.application_id == application.id)
                .order_by(Claim.block_index, Claim.span_start, Claim.id)
            )
        ).scalars()
    )
    sources = await _sources(db, application.user_id, claims)
    detail = application.ats_after_detail_json
    return ApplicationOut(
        id=application.id,
        job_id=job.id,
        status=application.status,
        method=application.method,
        note=application.tailor_note,
        blocks=[
            CvBlockOut(
                kind=b.kind,
                section=b.section,
                text=b.text,
                original=b.original,
                line_no=b.line_no,
                entry=b.entry,
                rewritten=b.rewritten,
            )
            for b in blocks_of(application)
        ],
        claims=[
            ClaimOut(
                id=c.id,
                block_index=c.block_index,
                text=c.text,
                type=c.type,
                span_start=c.span_start,
                span_end=c.span_end,
                bucket=c.bucket,
                origin=c.origin,
                similarity=c.similarity,
                user_decision=c.user_decision,
                source=sources.get(c.id),
            )
            for c in claims
        ],
        ats=AtsAfterOut(
            before=job.ats_before,
            after=application.ats_after,
            matched=list(detail.get("matched", [])),
            missing=list(detail.get("missing", [])),
            checks=dict(detail.get("checks", {})),
        ),
        questions_asked=application.questions_asked,
        needs_you=sum(1 for c in claims if c.bucket == "needs_you"),
        checked_automatically=sum(
            1 for c in claims if c.bucket in ("verified", "inferred") and c.user_decision is None
        ),
        blocked=sum(1 for c in claims if c.bucket == "blocked"),
        cv_approved_at=application.cv_approved_at,
        updated_at=application.updated_at,
    )


@router.get("/jobs/{job_id}/application")
async def get_application(job_id: int, principal: AnyPrincipal, db: Db) -> ApplicationOut:
    job = await _job(db, principal, job_id)
    return await _out(db, await _application(db, job), job)


@router.post("/jobs/{job_id}/tailor", dependencies=[Depends(rate_limit("ingest"))])
async def tailor_cv(
    job_id: int,
    principal: AnyPrincipal,
    db: Db,
    llm: LLM,
    taxonomy: Tax,
    similarity: Sim,
    settings: AppSettings,
) -> ApplicationOut:
    job = await _job(db, principal, job_id)
    try:
        application = await prepare(
            db,
            principal.user,
            job,
            llm=llm,
            taxonomy=taxonomy,
            similarity=similarity,
            threshold=settings.infer_threshold,
            now=datetime.now(UTC),
        )
    except WorkspaceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return await _out(db, application, job)


@router.post("/claims/{claim_id}/decision")
async def decide_claim(
    claim_id: int,
    body: ClaimDecisionIn,
    principal: AnyPrincipal,
    db: Db,
    taxonomy: Tax,
    similarity: Sim,
    settings: AppSettings,
) -> ApplicationOut:
    claim = await db.get(Claim, claim_id)
    if claim is None or claim.user_id != principal.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found.")
    application = await db.get(Application, claim.application_id)
    assert application is not None  # claims cascade with their application
    job = await _job(db, principal, application.job_id)
    try:
        await decide(
            db,
            application,
            job,
            claim,
            body.action,
            url=str(body.url) if body.url else None,
            note=body.note,
            text=body.text,
            taxonomy=taxonomy,
            similarity=similarity,
            threshold=settings.infer_threshold,
            now=datetime.now(UTC),
        )
    except DecisionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await db.flush()
    return await _out(db, application, job)


@router.post("/jobs/{job_id}/application/approve")
async def approve(job_id: int, principal: AnyPrincipal, db: Db) -> ApplicationOut:
    job = await _job(db, principal, job_id)
    application = await _application(db, job)
    try:
        await approve_cv(db, application, datetime.now(UTC))
    except WorkspaceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return await _out(db, application, job)


@router.get("/jobs/{job_id}/application/cv.pdf")
async def cv_pdf(job_id: int, principal: AnyPrincipal, db: Db) -> Response:
    job = await _job(db, principal, job_id)
    application = await _application(db, job)
    if application.status == "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Approve the CV first; then it can be downloaded."
        )
    pdf = render_cv(blocks_of(application), title=f"CV for {job.title}")
    filename = "".join(ch if ch.isalnum() else "-" for ch in f"cv-{job.company}")[:60]
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}.pdf"',
            "Cache-Control": "no-store",
        },
    )
