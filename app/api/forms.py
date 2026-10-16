"""Apply by form (Flow D): find the form's questions, draft answers, let the user edit and
approve each, and mark the application submitted once they have submitted it themselves."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.applications import Sim, application_for, application_out, job_for
from app.api.deps import LLM, AnyPrincipal, AppSettings, Db, Fetcher, Locations, Tax
from app.api.ratelimit import rate_limit
from app.api.schemas import AnswerEditIn, ApplicationOut, PasteQuestionsIn
from app.integrations.forms import FormDiscoveryError
from app.services import form_apply
from app.services.applications import WorkspaceError

router = APIRouter(prefix="/api/jobs/{job_id}/application", tags=["form"])


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.post("/questions/discover", dependencies=[Depends(rate_limit("ingest"))])
async def discover_questions(
    job_id: int, request: Request, principal: AnyPrincipal, db: Db, fetcher: Fetcher
) -> ApplicationOut:
    job = await job_for(db, principal, job_id)
    application = await application_for(db, job)
    try:
        await form_apply.discover_questions(
            db,
            application,
            job,
            http=request.app.state.http,
            fetcher=fetcher,
            now=datetime.now(UTC),
        )
    except (WorkspaceError, FormDiscoveryError) as exc:
        raise _conflict(exc) from exc
    return await application_out(db, application, job)


@router.post("/questions/paste")
async def paste_questions(
    job_id: int, body: PasteQuestionsIn, principal: AnyPrincipal, db: Db
) -> ApplicationOut:
    job = await job_for(db, principal, job_id)
    application = await application_for(db, job)
    try:
        await form_apply.paste_questions(db, application, job, body.text, now=datetime.now(UTC))
    except WorkspaceError as exc:
        raise _conflict(exc) from exc
    return await application_out(db, application, job)


@router.post("/answers/draft", dependencies=[Depends(rate_limit("ingest"))])
async def draft_answers(
    job_id: int,
    request: Request,
    principal: AnyPrincipal,
    db: Db,
    llm: LLM,
    taxonomy: Tax,
    similarity: Sim,
    settings: AppSettings,
    locations: Locations,
) -> ApplicationOut:
    job = await job_for(db, principal, job_id)
    application = await application_for(db, job)
    try:
        await form_apply.draft_answers(
            db,
            principal.user,
            job,
            application,
            llm=llm,
            taxonomy=taxonomy,
            similarity=similarity,
            threshold=settings.infer_threshold,
            locations=locations,
            tavily=request.app.state.tavily,
            now=datetime.now(UTC),
        )
    except WorkspaceError as exc:
        raise _conflict(exc) from exc
    await db.flush()
    return await application_out(db, application, job)


@router.put("/answers/{index}")
async def edit_answer(
    job_id: int,
    index: int,
    body: AnswerEditIn,
    principal: AnyPrincipal,
    db: Db,
    llm: LLM,
    taxonomy: Tax,
    similarity: Sim,
    settings: AppSettings,
) -> ApplicationOut:
    job = await job_for(db, principal, job_id)
    application = await application_for(db, job)
    try:
        await form_apply.edit_answer(
            db,
            principal.user,
            job,
            application,
            index,
            body.answer,
            remember=body.remember,
            llm=llm,
            taxonomy=taxonomy,
            similarity=similarity,
            threshold=settings.infer_threshold,
            now=datetime.now(UTC),
        )
    except WorkspaceError as exc:
        raise _conflict(exc) from exc
    await db.flush()
    return await application_out(db, application, job)


@router.post("/answers/{index}/approve")
async def approve_answer(
    job_id: int, index: int, principal: AnyPrincipal, db: Db
) -> ApplicationOut:
    job = await job_for(db, principal, job_id)
    application = await application_for(db, job)
    try:
        await form_apply.approve_answer(db, application, index, datetime.now(UTC))
    except WorkspaceError as exc:
        raise _conflict(exc) from exc
    return await application_out(db, application, job)


@router.post("/submitted")
async def mark_submitted(job_id: int, principal: AnyPrincipal, db: Db) -> ApplicationOut:
    """Gate 4: the user says they submitted the form on the employer's site."""
    job = await job_for(db, principal, job_id)
    application = await application_for(db, job)
    try:
        await form_apply.mark_submitted(db, job, application, datetime.now(UTC))
    except WorkspaceError as exc:
        raise _conflict(exc) from exc
    return await application_out(db, application, job)
