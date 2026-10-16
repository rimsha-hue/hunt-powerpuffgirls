"""Apply Workspace for one job (Flow B): tailor the CV, verify every claim, score ATS after, take
the user's Gate 2 answers, and approve the CV once nothing unproven is left."""

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Application, Claim, Job, ResumeLine, User
from app.domain.ats import AtsResult, Keyword, cv_from_lines, score_cv, supported_keys
from app.domain.claims import Ledger
from app.domain.cv import Block, SourceLine, base_blocks, cv_document
from app.domain.status import transition
from app.domain.taxonomy import Taxonomy
from app.integrations.embeddings import Similarity
from app.integrations.llm import StructuredLLM
from app.services import audit, verifier
from app.services.tailor import tailor
from app.services.verifier import DecisionError, load_ledger

logger = structlog.get_logger()

Decision = Literal["confirm", "edit", "remove", "false"]


class WorkspaceError(Exception):
    """Safe to show to the user."""


async def source_lines(db: AsyncSession, user_id: int) -> list[SourceLine]:
    rows = await db.execute(
        select(ResumeLine).where(ResumeLine.user_id == user_id).order_by(ResumeLine.line_no)
    )
    return [SourceLine(r.id, r.line_no, r.section, r.text, r.bulleted) for r in rows.scalars()]


Document = Literal["cv", "email", "cover_letter"]


def blocks_of(application: Application, document: str = "cv") -> list[Block]:
    data = {
        "cv": application.tailored_cv_json,
        "email": application.email_blocks_json,
        "cover_letter": application.cover_letter_blocks_json,
    }[document]
    return [Block.from_json(b) for b in data]


def set_blocks(application: Application, document: str, blocks: Sequence[Block]) -> None:
    data = [b.to_json() for b in blocks]
    if document == "cv":
        application.tailored_cv_json = data
    elif document == "email":
        application.email_blocks_json = data
    else:
        application.cover_letter_blocks_json = data


def ats_after(
    job: Job,
    blocks: Sequence[Block],
    lines: Sequence[SourceLine],
    ledger: Ledger,
    taxonomy: Taxonomy,
) -> AtsResult:
    keywords = [Keyword.from_json(k) for k in job.ats_keywords_json]
    original = cv_from_lines((line.section, line.text) for line in lines)
    allowed = supported_keys(keywords, original, ledger.values.get("skill", frozenset()), taxonomy)
    return score_cv(keywords, cv_document(blocks), taxonomy, allowed)


def _advance(db: AsyncSession, job: Job, target: str, user_id: int, now: datetime) -> None:
    """Moves the job forward through the gates it skipped (new -> shortlisted -> in_review)."""
    path = {"new": ["shortlisted", "in_review"], "shortlisted": ["in_review"]}.get(job.status, [])
    for step in path:
        job.status = transition(job.status, step)
        job.status_changed_at = now
        audit.record(
            db,
            user_id=user_id,
            actor="user",
            action="job_status",
            entity="job",
            entity_id=job.id,
            to=step,
        )
        if step == target:
            break


async def prepare(
    db: AsyncSession,
    user: User,
    job: Job,
    *,
    llm: StructuredLLM,
    taxonomy: Taxonomy,
    similarity: Similarity,
    threshold: float,
    now: datetime,
) -> Application:
    """Tailors and verifies (again, if the CV isn't approved yet) the CV for this job."""
    application = (
        await db.execute(select(Application).where(Application.job_id == job.id))
    ).scalar_one_or_none()
    if application is not None and application.status != "draft":
        raise WorkspaceError("This CV is already approved. It can't be tailored again.")
    if job.status not in ("new", "shortlisted", "in_review"):
        raise WorkspaceError(
            f"This job is {job.status.replace('_', ' ')}, so it can't be tailored."
        )
    lines = await source_lines(db, user.id)
    if not lines:
        raise WorkspaceError("Upload your resume in Onboarding first.")

    keywords = [Keyword.from_json(k) for k in job.ats_keywords_json]
    tailored = await tailor(
        base_blocks(lines),
        job_title=job.title,
        company=job.company,
        description=job.description,
        keywords=keywords,
        llm=llm,
        taxonomy=taxonomy,
    )
    ledger, employers = await load_ledger(db, user.id)
    checked = await verifier.verify(
        tailored.blocks,
        ledger=ledger,
        employers=employers,
        taxonomy=taxonomy,
        llm=llm,
        similarity=similarity,
        threshold=threshold,
    )

    if application is None:
        application = Application(
            user_id=user.id, job_id=job.id, method=job.apply_method, created_at=now
        )
        db.add(application)
    else:
        await db.execute(delete(Claim).where(Claim.application_id == application.id))
    application.tailored_cv_json = [b.to_json() for b in checked.blocks]
    notes = [n for n in (tailored.note, checked.note) if n]
    if similarity.method != "embedding":
        notes.append("Similarity ran in its simpler word-overlap mode, so more claims came to you.")
    application.tailor_note = " ".join(notes)[:300] or None
    application.resume_version = user.profile.resume_version
    application.updated_at = now
    await db.flush()

    rows = verifier.claim_rows(application.id, user.id, checked.drafts)
    db.add_all(rows)
    application.questions_asked = sum(1 for r in rows if r.bucket == "needs_you")
    result = ats_after(job, checked.blocks, lines, ledger, taxonomy)
    application.ats_after = result.score
    application.ats_after_detail_json = result.detail_json()
    _advance(db, job, "in_review", user.id, now)
    audit.record(
        db,
        user_id=user.id,
        actor="tailor",
        action="cv_tailored",
        entity="application",
        entity_id=application.id,
        rewritten=tailored.rewritten,
        claims=len(rows),
        needs_you=application.questions_asked,
        blocked=sum(1 for r in rows if r.bucket == "blocked"),
    )
    return application


async def decide(
    db: AsyncSession,
    application: Application,
    job: Job,
    claim: Claim,
    action: Decision,
    *,
    url: str | None,
    note: str | None,
    text: str | None,
    taxonomy: Taxonomy,
    similarity: Similarity,
    threshold: float,
    now: datetime,
) -> None:
    """Gates 2 and 3: the user's answer to one claim in the CV, email or cover letter. Only open
    (Needs-you) claims take answers, and only while their document can still change."""
    document = claim.document
    if document == "cv" and application.status != "draft":
        raise DecisionError("This CV is already approved.")
    if application.sent_at is not None:
        raise DecisionError("This application was already sent.")
    if claim.bucket != "needs_you":
        raise DecisionError("Only claims that need you can be answered.")
    # Block numbers are per document, so only this document's claims move together.
    claims = list(
        (
            await db.execute(
                select(Claim).where(
                    Claim.application_id == application.id, Claim.document == document
                )
            )
        ).scalars()
    )
    claim = next(c for c in claims if c.id == claim.id)
    blocks = blocks_of(application, document)
    if action == "confirm":
        await verifier.confirm(db, claim, claims, url=url, note=note, now=now)
    elif action == "remove":
        verifier.remove(blocks, claim, claims, now=now, decision="removed")
        audit.record(
            db,
            user_id=claim.user_id,
            actor="user",
            action="claim_removed",
            entity="claim",
            entity_id=claim.id,
        )
    elif action == "false":
        await verifier.mark_false(db, blocks, claim, claims, now=now)
    else:
        ledger, _ = await load_ledger(db, claim.user_id)
        await verifier.edit(
            blocks,
            claim,
            claims,
            text or "",
            ledger=ledger,
            taxonomy=taxonomy,
            similarity=similarity,
            threshold=threshold,
            now=now,
        )
        audit.record(
            db,
            user_id=claim.user_id,
            actor="user",
            action="claim_edited",
            entity="claim",
            entity_id=claim.id,
            bucket=claim.bucket,
        )

    set_blocks(application, document, blocks)
    application.updated_at = now
    if document == "cv":
        ledger, _ = await load_ledger(db, application.user_id)
        lines = await source_lines(db, application.user_id)
        result = ats_after(job, blocks, lines, ledger, taxonomy)
        application.ats_after = result.score
        application.ats_after_detail_json = result.detail_json()


async def approve_cv(db: AsyncSession, application: Application, now: datetime) -> None:
    """Gate 2 passed: the CV may be rendered and used only once no claim needs the user."""
    open_claims = (
        await db.execute(
            select(Claim.id).where(
                Claim.application_id == application.id,
                Claim.document == "cv",
                Claim.bucket == "needs_you",
            )
        )
    ).all()
    if open_claims:
        raise WorkspaceError(f"{len(open_claims)} claim(s) still need you. Answer them first.")
    if application.status != "draft":
        return
    application.status = "cv_approved"
    application.cv_approved_at = now
    application.updated_at = now
    audit.record(
        db,
        user_id=application.user_id,
        actor="user",
        action="gate_verify_passed",
        entity="application",
        entity_id=application.id,
    )
