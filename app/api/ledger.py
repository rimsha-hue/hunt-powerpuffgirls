"""Evidence ledger: resume upload, GitHub sync, listing with sources, and deletion."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import or_, select

from app.api.deps import LLM, AppSettings, CurrentUser, Db, Fetcher, GitHub, Tax
from app.api.ratelimit import rate_limit
from app.api.schemas import (
    EvidenceOut,
    GitHubIngestionOut,
    PortfolioIngestionOut,
    ResumeIngestionOut,
    ResumeLineOut,
)
from app.assets import SAMPLE_RESUME
from app.db.models import Evidence, ResumeLine
from app.domain.uploads import UploadRejectedError
from app.integrations.fetch import FetchBlockedError, FetchError
from app.integrations.github import GitHubError
from app.services import audit
from app.services.ingestion import ingest_github, ingest_portfolio, ingest_resume

router = APIRouter(prefix="/api", tags=["ledger"])

SOURCE_LABELS = {
    "resume": "Resume",
    "github": "GitHub",
    "certificate": "Certificate",
    "portfolio": "Portfolio",
    "user_confirmed": "Confirmed by you",
}


async def evidence_out(db: Db, rows: list[Evidence]) -> list[EvidenceOut]:
    line_ids = [int(e.source_ref) for e in rows if e.source == "resume" and e.source_ref.isdigit()]
    lines: dict[int, ResumeLine] = {}
    if line_ids:
        result = await db.execute(select(ResumeLine).where(ResumeLine.id.in_(line_ids)))
        lines = {line.id: line for line in result.scalars()}

    out = []
    for e in rows:
        detail: str | None = None
        url: str | None = None
        if e.source == "resume" and e.source_ref.isdigit():
            line = lines.get(int(e.source_ref))
            detail = f"Line {line.line_no} ({line.section}): {line.text}" if line else None
        elif e.source_ref.startswith(("https://", "http://")):
            url = e.source_ref
        else:
            detail = e.source_ref or None
        out.append(
            EvidenceOut(
                id=e.id,
                claim_text=e.claim_text,
                type=e.type,
                normalized_value=e.normalized_value,
                source=e.source,
                source_ref=e.source_ref,
                source_label=SOURCE_LABELS.get(e.source, e.source),
                source_detail=detail,
                source_url=url,
                context=e.context,
                confirmed_by=e.confirmed_by,
                confidence=e.confidence,
                created_at=e.created_at,
            )
        )
    return out


@router.get("/ledger")
async def list_ledger(
    principal: CurrentUser,
    db: Db,
    q: Annotated[str | None, Query(max_length=100)] = None,
    type: Annotated[str | None, Query(max_length=20)] = None,  # noqa: A002
    source: Annotated[str | None, Query(max_length=20)] = None,
) -> list[EvidenceOut]:
    stmt = select(Evidence).where(Evidence.user_id == principal.user.id)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                Evidence.normalized_value.like(like),
                Evidence.claim_text.ilike(like),
                Evidence.context.ilike(like),
            )
        )
    if type:
        stmt = stmt.where(Evidence.type == type)
    if source:
        stmt = stmt.where(Evidence.source == source)
    stmt = stmt.order_by(Evidence.created_at.desc(), Evidence.id.desc())
    rows = list((await db.execute(stmt)).scalars())
    return await evidence_out(db, rows)


@router.delete("/ledger/{evidence_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_evidence(evidence_id: int, principal: CurrentUser, db: Db) -> None:
    evidence = await db.get(Evidence, evidence_id)
    # Same 404 for "missing" and "someone else's", so ids can't be probed.
    if evidence is None or evidence.user_id != principal.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fact not found.")
    await db.delete(evidence)
    audit.record(
        db,
        user_id=principal.user.id,
        actor="user",
        action="evidence_deleted",
        entity="evidence",
        entity_id=evidence_id,
        type=evidence.type,
        source=evidence.source,
    )


@router.get("/resume/lines")
async def resume_lines(principal: CurrentUser, db: Db) -> list[ResumeLineOut]:
    result = await db.execute(
        select(ResumeLine)
        .where(ResumeLine.user_id == principal.user.id)
        .order_by(ResumeLine.line_no)
    )
    return [ResumeLineOut.model_validate(line) for line in result.scalars()]


async def _ingest(
    data: bytes, principal: CurrentUser, db: Db, llm: LLM, taxonomy: Tax, settings: AppSettings
) -> ResumeIngestionOut:
    try:
        report = await ingest_resume(
            db,
            principal.user,
            data,
            max_bytes=settings.max_upload_mb * 1024 * 1024,
            llm=llm,
            taxonomy=taxonomy,
            phone_region=settings.default_phone_region,
        )
    except UploadRejectedError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ResumeIngestionOut(
        resume_version=report.resume_version,
        line_count=report.line_count,
        facts=await evidence_out(db, report.evidence),
        rejected_by_code=report.rejected_by_code,
        blocked=report.blocked,
        llm_used=report.llm_used,
        llm_note=report.llm_note,
    )


@router.post("/resume", dependencies=[Depends(rate_limit("ingest"))])
async def upload_resume(
    principal: CurrentUser,
    db: Db,
    llm: LLM,
    taxonomy: Tax,
    settings: AppSettings,
    file: Annotated[UploadFile, File(description="PDF or DOCX, 5 MB max")],
) -> ResumeIngestionOut:
    limit = settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)  # read one byte past the limit to detect oversize files
    return await _ingest(data, principal, db, llm, taxonomy, settings)


@router.post("/resume/sample", dependencies=[Depends(rate_limit("ingest"))])
async def load_sample_resume(
    principal: CurrentUser, db: Db, llm: LLM, taxonomy: Tax, settings: AppSettings
) -> ResumeIngestionOut:
    return await _ingest(SAMPLE_RESUME.read_bytes(), principal, db, llm, taxonomy, settings)


@router.post("/github/sync", dependencies=[Depends(rate_limit("ingest"))])
async def sync_github(
    principal: CurrentUser, db: Db, github: GitHub, taxonomy: Tax
) -> GitHubIngestionOut:
    if not principal.user.github_username:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add your GitHub username first.")
    try:
        report = await ingest_github(db, principal.user, github, taxonomy)
    except GitHubError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return GitHubIngestionOut(
        username=report.username,
        repos_scanned=report.repos_scanned,
        facts=await evidence_out(db, report.evidence),
    )


@router.post("/portfolio/sync", dependencies=[Depends(rate_limit("ingest"))])
async def sync_portfolio(
    principal: CurrentUser, db: Db, fetcher: Fetcher, llm: LLM, taxonomy: Tax, settings: AppSettings
) -> PortfolioIngestionOut:
    if not principal.user.portfolio_url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add your portfolio URL first.")
    try:
        report = await ingest_portfolio(
            db, principal.user, fetcher, llm, taxonomy, renderer_url=settings.jina_reader_url
        )
    except FetchBlockedError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except FetchError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return PortfolioIngestionOut(
        url=report.url,
        method=report.method,
        line_count=report.line_count,
        facts=await evidence_out(db, report.evidence),
        rejected_by_code=report.rejected_by_code,
        llm_note=report.llm_note,
    )
