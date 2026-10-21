"""Apply Workspace: tailor and verify the CV for a job, answer Needs-you claims (Gate 2), approve
the CV and download it as a PDF. An Apply-link session may work on its own job here."""

from datetime import UTC, datetime
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select

from app.api.deps import LLM, AnyPrincipal, AppSettings, Db, Principal, Tax, can_open_job
from app.api.ledger import evidence_out
from app.api.ratelimit import rate_limit
from app.api.schemas import (
    AnswerOut,
    AnswerSourceOut,
    ApplicationOut,
    AtsAfterOut,
    AttachmentOut,
    ClaimDecisionIn,
    ClaimOut,
    ClaimSourceOut,
    CompanyFactOut,
    CvBlockOut,
    CvCheckOut,
    CvFormatCheckOut,
    EmailEditIn,
    EmailOut,
    FormOut,
    TailorIn,
    WaiveIn,
)
from app.db.models import Application, Claim, Evidence, Job, ResumeLine, User
from app.domain.compose import prose_text
from app.domain.cv import Block
from app.domain.cv_format import CvTemplate
from app.domain.uploads import UploadRejectedError, sniff_resume
from app.integrations.embeddings import Similarity
from app.integrations.pdf import CheckedCv, checked_cv, render_letter
from app.services import composer, form_apply
from app.services.applications import WorkspaceError, approve_cv, blocks_of, decide, prepare
from app.services.verifier import DecisionError

router = APIRouter(prefix="/api", tags=["workspace"])


def get_similarity(request: Request) -> Similarity:
    similarity: Similarity = request.app.state.similarity
    return similarity


Sim = Annotated[Similarity, Depends(get_similarity)]


async def job_for(db: Db, principal: Principal, job_id: int) -> Job:
    job = await db.get(Job, job_id)
    if job is None or not can_open_job(principal, job.user_id, job.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")
    return job


async def application_for(db: Db, job: Job) -> Application:
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


async def application_out(db: Db, application: Application, job: Job) -> ApplicationOut:
    user = await db.get(User, application.user_id)
    assert user is not None  # applications cascade with their user
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
        blocks=_blocks_out(blocks_of(application)),
        claims=[
            ClaimOut(
                id=c.id,
                document=c.document,
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
                paired_with=c.pair_text,
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
        email=await _email_out(db, application, user) if application.email_blocks_json else None,
        form=_form_out(application) if application.answers_json else None,
    )


def _blocks_out(blocks: list[Block]) -> list[CvBlockOut]:
    return [
        CvBlockOut(
            kind=b.kind,
            section=b.section,
            text=b.text,
            original=b.original,
            line_no=b.line_no,
            entry=b.entry,
            rewritten=b.rewritten,
        )
        for b in blocks
    ]


def _form_out(application: Application) -> FormOut:
    submitted = application.status == "submitted"
    return FormOut(
        source=application.form_source,
        url=application.form_url,
        answers=[
            AnswerOut(
                index=index,
                label=item["label"],
                description=item.get("description", ""),
                required=item["required"],
                field_type=item["field_type"],
                options=list(item.get("options") or []),
                name=item.get("name", ""),
                type=item["type"],
                answer=item["answer"],
                status=item["status"],
                source=AnswerSourceOut(**item["source"]) if item.get("source") else None,
                company_fact=CompanyFactOut(**item["company_fact"])
                if item.get("company_fact")
                else None,
                note=item.get("note"),
                blocks=_blocks_out(blocks_of(application, f"q:{index}")),
            )
            for index, item in enumerate(application.answers_json)
        ],
        blockers=form_apply.pack_blockers(application),
        submitted_at=application.sent_at if submitted else None,
        follow_up_due=application.follow_up_due if submitted else None,
    )


async def _email_out(db: Db, application: Application, user: User) -> EmailOut:
    body = composer.email_text(application, user)
    return EmailOut(
        recipient=application.recipient,
        subject=application.email_subject,
        blocks=_blocks_out(blocks_of(application, "email")),
        signature=body.split("\n\nBest regards,\n", 1)[-1],
        cover_letter=_blocks_out(blocks_of(application, "cover_letter")),
        attachments=[
            AttachmentOut(
                kind=i["kind"], label=i["label"], status=i["status"], detail=i.get("detail")
            )
            for i in application.attachments_json
        ],
        note=application.compose_note,
        blockers=await composer.blockers(db, application),
        sent_at=application.sent_at,
        follow_up_due=application.follow_up_due,
    )


@router.get("/jobs/{job_id}/application")
async def get_application(job_id: int, principal: AnyPrincipal, db: Db) -> ApplicationOut:
    job = await job_for(db, principal, job_id)
    return await application_out(db, await application_for(db, job), job)


@router.post("/jobs/{job_id}/tailor", dependencies=[Depends(rate_limit("ingest"))])
async def tailor_cv(
    job_id: int,
    principal: AnyPrincipal,
    db: Db,
    llm: LLM,
    taxonomy: Tax,
    similarity: Sim,
    settings: AppSettings,
    body: TailorIn | None = None,
) -> ApplicationOut:
    """Tailors the CV; with `preferences`, revises it the way the user asked."""
    job = await job_for(db, principal, job_id)
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
            preferences=body.preferences if body else "",
        )
    except WorkspaceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return await application_out(db, application, job)


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
    job = await job_for(db, principal, application.job_id)
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
            whole=body.whole,
        )
    except DecisionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await db.flush()
    return await application_out(db, application, job)


@router.post("/jobs/{job_id}/application/approve")
async def approve(job_id: int, principal: AnyPrincipal, db: Db) -> ApplicationOut:
    job = await job_for(db, principal, job_id)
    application = await application_for(db, job)
    try:
        await approve_cv(db, application, datetime.now(UTC))
    except WorkspaceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return await application_out(db, application, job)


async def _checked_cv(
    db: Db, principal: Principal, job_id: int, template: CvTemplate
) -> tuple[Job, CheckedCv]:
    job = await job_for(db, principal, job_id)
    application = await application_for(db, job)
    if application.status == "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Approve the CV first; then it can be downloaded."
        )
    blocks = blocks_of(application)
    # Rendering and reading back are CPU work; keep them off the event loop.
    result = await anyio.to_thread.run_sync(
        lambda: checked_cv(blocks, title=f"CV for {job.title}", preferred=template)
    )
    return job, result


@router.get("/jobs/{job_id}/application/cv.pdf")
async def cv_pdf(
    job_id: int, principal: AnyPrincipal, db: Db, template: CvTemplate = "classic"
) -> Response:
    job, cv = await _checked_cv(db, principal, job_id, template)
    filename = "".join(ch if ch.isalnum() else "-" for ch in f"cv-{job.company}")[:60]
    return Response(
        cv.pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}.pdf"',
            "Cache-Control": "no-store",
            "X-CV-Template": cv.template,
        },
    )


@router.get("/jobs/{job_id}/application/cv-check")
async def cv_check(
    job_id: int, principal: AnyPrincipal, db: Db, template: CvTemplate = "classic"
) -> CvCheckOut:
    """The format checks for the CV in a template, and the template the download will use."""
    _, cv = await _checked_cv(db, principal, job_id, template)
    return CvCheckOut(
        requested=template,
        template=cv.template,
        checks=[CvFormatCheckOut(name=c.name, passed=c.passed, detail=c.detail) for c in cv.checks],
    )


@router.post("/jobs/{job_id}/application/compose", dependencies=[Depends(rate_limit("ingest"))])
async def compose_email(
    job_id: int,
    principal: AnyPrincipal,
    db: Db,
    llm: LLM,
    taxonomy: Tax,
    similarity: Sim,
    settings: AppSettings,
) -> ApplicationOut:
    """Flow C: draft the email (and cover letter if asked for) from the approved CV, verified."""
    job = await job_for(db, principal, job_id)
    application = await application_for(db, job)
    try:
        await composer.compose(
            db,
            principal.user,
            job,
            application,
            llm=llm,
            taxonomy=taxonomy,
            similarity=similarity,
            threshold=settings.infer_threshold,
            now=datetime.now(UTC),
        )
    except WorkspaceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await db.flush()
    return await application_out(db, application, job)


@router.put("/jobs/{job_id}/application/email")
async def edit_email(
    job_id: int,
    body: EmailEditIn,
    principal: AnyPrincipal,
    db: Db,
    llm: LLM,
    taxonomy: Tax,
    similarity: Sim,
    settings: AppSettings,
) -> ApplicationOut:
    """The user edits the recipient, subject, body or cover letter. New wording is verified."""
    job = await job_for(db, principal, job_id)
    application = await application_for(db, job)
    if application.sent_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This application was already sent.")
    if not application.email_blocks_json:
        raise HTTPException(status.HTTP_409_CONFLICT, "Draft the email first.")
    if body.recipient is not None:
        application.recipient = str(body.recipient).lower()
    if body.subject is not None:
        application.email_subject = " ".join(body.subject.split())
    options = {
        "llm": llm,
        "taxonomy": taxonomy,
        "similarity": similarity,
        "threshold": settings.infer_threshold,
        "now": datetime.now(UTC),
        "ignore": (job.title, job.company),
        "subject": job.company,
    }
    try:
        if body.body is not None:
            await composer.edit_prose(db, application, "email", body.body, **options)  # type: ignore[arg-type]
        if body.cover_letter is not None and application.cover_letter_blocks_json:
            await composer.edit_prose(db, application, "cover_letter", body.cover_letter, **options)  # type: ignore[arg-type]
    except WorkspaceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await db.flush()
    return await application_out(db, application, job)


@router.post(
    "/jobs/{job_id}/application/files/{kind}", dependencies=[Depends(rate_limit("ingest"))]
)
async def upload_file(
    job_id: int,
    kind: str,
    principal: AnyPrincipal,
    db: Db,
    settings: AppSettings,
    file: Annotated[UploadFile, File(description="PDF or DOCX")],
) -> ApplicationOut:
    job = await job_for(db, principal, job_id)
    application = await application_for(db, job)
    limit = settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    try:
        file_kind = sniff_resume(data, limit)
        await composer.add_file(
            db,
            application,
            kind,
            filename=file.filename or kind,
            file_kind=file_kind,
            data=data,
            now=datetime.now(UTC),
        )
    except UploadRejectedError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except WorkspaceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return await application_out(db, application, job)


@router.post("/jobs/{job_id}/application/attachments/{kind}/waive")
async def waive_attachment(
    job_id: int, kind: str, body: WaiveIn, principal: AnyPrincipal, db: Db
) -> ApplicationOut:
    """The user explicitly sends without an item the posting asked for (or takes that back)."""
    job = await job_for(db, principal, job_id)
    application = await application_for(db, job)
    try:
        composer.waive(application, kind, body.waived)
    except WorkspaceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return await application_out(db, application, job)


@router.get("/jobs/{job_id}/application/cover-letter.pdf")
async def cover_letter_pdf(job_id: int, principal: AnyPrincipal, db: Db) -> Response:
    job = await job_for(db, principal, job_id)
    application = await application_for(db, job)
    letter = blocks_of(application, "cover_letter")
    if not letter:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No cover letter for this application.")
    user = principal.user
    pdf = render_letter(
        sender=user.name or "",
        contact=user.email,
        body=f"{prose_text(letter)}\n\nSincerely,\n{user.name}",
        title="Cover letter",
    )
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="cover-letter.pdf"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/jobs/{job_id}/application/send", dependencies=[Depends(rate_limit("ingest"))])
async def send_application(
    job_id: int, request: Request, principal: AnyPrincipal, db: Db, settings: AppSettings
) -> ApplicationOut:
    """Gate 3 (Approve): the email goes out from the user's mailbox with its attachments."""
    job = await job_for(db, principal, job_id)
    application = await application_for(db, job)
    try:
        await composer.send(
            db,
            principal.user,
            job,
            application,
            email=request.app.state.email,
            daily_limit=settings.email_daily_limit,
            now=datetime.now(UTC),
        )
    except WorkspaceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return await application_out(db, application, job)
