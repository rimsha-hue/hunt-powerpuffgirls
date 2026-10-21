"""Composer and Courier for applying by email (Flow C).

The Composer drafts the email (and a cover letter when the posting asks for one) from the approved
CV only. Every sentence is checked by the Verifier against the resume line closest to it. Gate 3
sends nothing until the CV is approved, no claim needs the user, and every required attachment is
attached, uploaded or waived.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Application, ApplicationFile, Claim, Job, User
from app.domain.compose import (
    COVER_MAX_WORDS,
    COVER_MIN_WORDS,
    EMAIL_MAX_WORDS,
    EMAIL_MIN_WORDS,
    AttachmentItem,
    attachments_ready,
    plan_attachments,
    prose_blocks,
    prose_problems,
    prose_text,
    sentences,
    signature,
    subject_for,
)
from app.domain.cv import Block, SourceLine, plain
from app.domain.status import InvalidTransitionError, transition
from app.domain.style import BANNED_PHRASES
from app.domain.taxonomy import Taxonomy
from app.integrations.email import Attachment, EmailError, EmailProvider, OutgoingEmail
from app.integrations.embeddings import Similarity
from app.integrations.llm import LLMError, StructuredLLM
from app.integrations.pdf import checked_cv, render_letter
from app.services import audit, mailer, verifier
from app.services.applications import WorkspaceError, blocks_of, set_blocks, source_lines

logger = structlog.get_logger()

FOLLOW_UP_DAYS = 7
# Below this similarity a sentence has no source line (greetings, closings, plain requests).
MIN_SOURCE_SIMILARITY = 0.35
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_RULES = (
    "Use only facts from the approved CV; never add a skill, number, employer, degree or result it "
    "doesn't state, and keep numbers exactly as written. Plain, specific and warm; vary sentence "
    "length. Never use em or en dashes, and never use these words: "
    + ", ".join(BANNED_PHRASES)
    + "."
)
EMAIL_SYSTEM = (
    "You write a short job application email for a candidate, in their voice. " + _RULES + " "
    f"The body is {EMAIL_MIN_WORDS} to {EMAIL_MAX_WORDS} words: a greeting line, the role being "
    "applied for, two or three concrete results from the CV that fit the job, a line saying the CV "
    "is attached, and a short closing sentence. No sign-off and no signature; they are added."
)
COVER_SYSTEM = (
    "You write a cover letter for a candidate, in their voice. " + _RULES + " "
    f"It is {COVER_MIN_WORDS} to {COVER_MAX_WORDS} words in three or four short paragraphs, opening "
    "with a greeting line. No sign-off and no signature; they are added."
)


class Prose(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@dataclass
class Draft:
    text: str
    note: str | None


def _contact(user: User) -> tuple[str, list[str]]:
    profile = user.profile
    links = [
        link
        for link in (
            profile.linkedin,
            f"https://github.com/{user.github_username}" if user.github_username else None,
            user.portfolio_url,
        )
        if link
    ]
    return " | ".join([user.email, *([profile.phone] if profile.phone else [])]), links


def _fallback(kind: str, job: Job, cv: Sequence[Block]) -> str:
    """Without an LLM: a plain letter built from the user's own CV lines, word for word."""
    best = [b.text.rstrip(".") + "." for b in cv if b.kind == "bullet" and b.text][:3]
    attached = "My CV is attached" + (" with a cover letter." if kind == "email" else ".")
    return "\n\n".join(
        [
            "Dear Hiring Manager,",
            f"I am applying for the {job.title} role at {job.company}. " + " ".join(best),
            f"{attached} Thank you for your time, and I would be glad to talk.",
        ]
    )


async def _draft(kind: str, job: Job, cv: Sequence[Block], llm: StructuredLLM, extra: str) -> Draft:
    low, high = (
        (EMAIL_MIN_WORDS, EMAIL_MAX_WORDS)
        if kind == "email"
        else (COVER_MIN_WORDS, COVER_MAX_WORDS)
    )
    label = "email" if kind == "email" else "cover letter"
    if not llm.configured:
        return Draft(
            _fallback(kind, job, cv), f"AI writing is off, so the {label} uses your CV lines."
        )
    data = {
        "job": f"{job.title} at {job.company}\n\n{job.description[:5000]}",
        "approved_cv": "\n".join(b.text for b in cv if b.text),
    }
    system = EMAIL_SYSTEM if kind == "email" else COVER_SYSTEM
    task = f"Write the {label} body. {extra}".strip()
    try:
        draft = await llm.generate(schema=Prose, system=system, task=task, data=data)
        problems = prose_problems(draft.body, low, high)
        if problems:  # one rewrite, then the user decides (spec section 8)
            draft = await llm.generate(
                schema=Prose,
                system=system,
                task=f"{task} Your last draft {'; '.join(problems)}. Fix that.",
                data={**data, "last_draft": draft.body},
            )
            problems = prose_problems(draft.body, low, high)
    except LLMError as exc:
        logger.warning("composer_llm_failed", kind=kind, error_type=type(exc).__name__)
        return Draft(
            _fallback(kind, job, cv), f"AI writing failed ({exc}) The {label} uses your CV lines."
        )
    note = f"The {label} still {'; '.join(problems)}. Edit it before sending." if problems else None
    return Draft(draft.body.strip(), note)


async def _closest_lines(
    texts: Sequence[str], lines: Sequence[SourceLine], similarity: Similarity
) -> list[tuple[int, int, str] | None]:
    """For each sentence, the resume line it is most similar to, if any is close enough."""
    candidates = [line for line in lines if line.section != "header" and len(line.text.split()) > 2]
    if not candidates:
        return [None for _ in texts]
    scores = await similarity.scores([(t, line.text) for t in texts for line in candidates])
    out: list[tuple[int, int, str] | None] = []
    for i in range(len(texts)):
        row = scores[i * len(candidates) : (i + 1) * len(candidates)]
        best = max(range(len(candidates)), key=row.__getitem__)
        line = candidates[best]
        out.append(
            (line.id, line.line_no, line.text) if row[best] >= MIN_SOURCE_SIMILARITY else None
        )
    return out


async def verify_prose(
    db: AsyncSession,
    application: Application,
    document: str,
    text: str,
    *,
    lines: Sequence[SourceLine],
    llm: StructuredLLM,
    taxonomy: Taxonomy,
    similarity: Similarity,
    threshold: float,
    ignore: Sequence[str] = (),
    context: Sequence[str] = (),
    subject: str = "",
) -> int:
    """Splits the text into sentence blocks, verifies them, and replaces the document's claims.
    Returns how many claims need the user."""
    text = plain(text)
    parts = [sentence for _, sentence in sentences(text)]
    blocks = prose_blocks(text, document, await _closest_lines(parts, lines, similarity))
    ledger = await verifier.load_ledger(db, application.user_id)
    checked = await verifier.verify(
        blocks,
        ignore=ignore,
        context=context,
        subject=subject,
        ledger=ledger,
        taxonomy=taxonomy,
        llm=llm,
        similarity=similarity,
        threshold=threshold,
    )
    await db.execute(
        delete(Claim).where(Claim.application_id == application.id, Claim.document == document)
    )
    set_blocks(application, document, checked.blocks)
    rows = verifier.claim_rows(application.id, application.user_id, checked.drafts, document)
    db.add_all(rows)
    return sum(1 for r in rows if r.bucket == "needs_you")


async def compose(
    db: AsyncSession,
    user: User,
    job: Job,
    application: Application,
    *,
    llm: StructuredLLM,
    taxonomy: Taxonomy,
    similarity: Similarity,
    threshold: float,
    now: datetime,
) -> None:
    if application.status == "draft":
        raise WorkspaceError("Approve your CV first; the email is written from it.")
    if application.sent_at is not None:
        raise WorkspaceError("This application was already sent.")
    cv = blocks_of(application)
    lines = await source_lines(db, user.id)
    name = user.name or next((b.text for b in cv if b.section == "header" and b.text), "")
    wants_letter = "cover_letter" in job.required_attachments
    email = await _draft(
        "email", job, cv, llm, "Mention the attached cover letter." if wants_letter else ""
    )
    notes = [n for n in (email.note,) if n]
    asked = await verify_prose(
        db,
        application,
        "email",
        email.text,
        lines=lines,
        llm=llm,
        taxonomy=taxonomy,
        similarity=similarity,
        threshold=threshold,
        ignore=(job.title, job.company),
        subject=job.company,
    )
    if wants_letter:
        letter = await _draft("cover_letter", job, cv, llm, "")
        notes += [letter.note] if letter.note else []
        asked += await verify_prose(
            db,
            application,
            "cover_letter",
            letter.text,
            lines=lines,
            llm=llm,
            taxonomy=taxonomy,
            similarity=similarity,
            threshold=threshold,
            ignore=(job.title, job.company),
            subject=job.company,
        )
    else:
        set_blocks(application, "cover_letter", [])
        await db.execute(
            delete(Claim).where(
                Claim.application_id == application.id, Claim.document == "cover_letter"
            )
        )

    application.email_subject = subject_for(title=job.title, name=name, description=job.description)
    application.recipient = application.recipient or job.apply_email
    uploaded = {
        f.kind: f
        for f in (
            await db.execute(
                select(ApplicationFile).where(ApplicationFile.application_id == application.id)
            )
        ).scalars()
    }
    items = []
    for item in plan_attachments(job.required_attachments, portfolio_url=user.portfolio_url):
        if item.status == "missing" and item.kind in uploaded:
            f = uploaded[item.kind]
            item = AttachmentItem(item.kind, item.label, "uploaded", f.filename, f.id)
        items.append(item)
    application.attachments_json = [i.to_json() for i in items]
    application.compose_note = " ".join(notes)[:500] or None
    application.questions_asked += asked
    application.updated_at = now
    audit.record(
        db,
        user_id=user.id,
        actor="composer",
        action="email_drafted",
        entity="application",
        entity_id=application.id,
        needs_you=asked,
        cover_letter=wants_letter,
    )


async def edit_prose(
    db: AsyncSession,
    application: Application,
    document: str,
    text: str,
    *,
    llm: StructuredLLM,
    taxonomy: Taxonomy,
    similarity: Similarity,
    threshold: float,
    now: datetime,
    ignore: Sequence[str] = (),
    subject: str = "",
) -> None:
    """The user's own wording replaces the draft and is verified the same way."""
    if application.sent_at is not None:
        raise WorkspaceError("This application was already sent.")
    lines = await source_lines(db, application.user_id)
    await verify_prose(
        db,
        application,
        document,
        text,
        lines=lines,
        llm=llm,
        taxonomy=taxonomy,
        similarity=similarity,
        threshold=threshold,
        ignore=ignore,
        subject=subject,
    )
    application.updated_at = now


def _set_item(application: Application, kind: str, item: AttachmentItem) -> None:
    items = [AttachmentItem.from_json(i) for i in application.attachments_json]
    if not any(i.kind == kind for i in items):
        raise WorkspaceError("This posting doesn't ask for that item.")
    application.attachments_json = [(item if i.kind == kind else i).to_json() for i in items]


async def add_file(
    db: AsyncSession,
    application: Application,
    kind: str,
    *,
    filename: str,
    file_kind: str,
    data: bytes,
    now: datetime,
) -> None:
    if application.sent_at is not None:
        raise WorkspaceError("This application was already sent.")
    items = {i["kind"]: i for i in application.attachments_json}
    if kind not in items or kind in ("cv", "cover_letter"):
        raise WorkspaceError("This posting doesn't ask for that item.")
    await db.execute(
        delete(ApplicationFile).where(
            ApplicationFile.application_id == application.id, ApplicationFile.kind == kind
        )
    )
    safe = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in filename)[:100] or kind
    row = ApplicationFile(
        application_id=application.id,
        user_id=application.user_id,
        kind=kind,
        filename=safe,
        content_type="application/pdf" if file_kind == "pdf" else DOCX_TYPE,
        data=data,
        created_at=now,
    )
    db.add(row)
    await db.flush()
    _set_item(
        application, kind, AttachmentItem(kind, items[kind]["label"], "uploaded", safe, row.id)
    )


def waive(application: Application, kind: str, waived: bool) -> None:
    if application.sent_at is not None:
        raise WorkspaceError("This application was already sent.")
    item = next(
        (AttachmentItem.from_json(i) for i in application.attachments_json if i["kind"] == kind),
        None,
    )
    if item is None or kind in ("cv", "cover_letter"):
        raise WorkspaceError("Only items you would add yourself can be waived.")
    if waived and item.status in ("missing",):
        _set_item(application, kind, AttachmentItem(kind, item.label, "waived"))
    elif not waived and item.status == "waived":
        _set_item(application, kind, AttachmentItem(kind, item.label, "missing"))


async def blockers(db: AsyncSession, application: Application) -> list[str]:
    """Why Gate 3 can't pass yet (empty when it can)."""
    reasons = []
    if application.status == "draft":
        reasons.append("Approve the CV.")
    if not application.email_blocks_json:
        reasons.append("Draft the email.")
    if not application.recipient:
        reasons.append("Add the recipient's email address.")
    open_claims = (
        await db.execute(
            select(Claim.id).where(
                Claim.application_id == application.id, Claim.bucket == "needs_you"
            )
        )
    ).all()
    if open_claims:
        reasons.append(f"Answer {len(open_claims)} claim(s) that need you.")
    items = [AttachmentItem.from_json(i) for i in application.attachments_json]
    if not attachments_ready(items):
        missing = ", ".join(i.label for i in items if i.status == "missing")
        reasons.append(f"Add or waive: {missing}.")
    return reasons


def email_text(application: Application, user: User) -> str:
    contact, links = _contact(user)
    body = prose_text(blocks_of(application, "email"))
    name = user.name or ""
    return f"{body}\n\nBest regards,\n{signature(name, contact, links)}"


async def send(
    db: AsyncSession,
    user: User,
    job: Job,
    application: Application,
    *,
    email: EmailProvider,
    daily_limit: int,
    now: datetime,
) -> None:
    """Gate 3: the user approved; the email goes out with its attachments. Sending twice is
    impossible: a sent application refuses, and nothing is marked sent if sending fails."""
    if application.sent_at is not None:
        raise WorkspaceError("This application was already sent.")
    reasons = await blockers(db, application)
    if reasons:
        raise WorkspaceError(" ".join(reasons))
    if not email.configured:
        raise WorkspaceError("Email isn't set up on this server.")
    if not user.email_verified:
        # Replies go to the user's address; only a proven owner may send from it.
        raise WorkspaceError("Verify your email first; the link is in your inbox.")
    try:
        await mailer.check_limit(db, user.id, limit=daily_limit, now=now)
    except mailer.SendLimitError as exc:
        raise WorkspaceError(str(exc)) from exc

    files: list[Attachment] = []
    safe_company = "".join(ch if ch.isalnum() else "-" for ch in job.company)[:40]
    name = user.name or "CV"
    files.append(
        Attachment(
            f"{name} CV {safe_company}.pdf",
            "application/pdf",
            # The emailed CV passes the same format checks as a download.
            checked_cv(
                blocks_of(application), title=f"CV for {job.title}", preferred="classic"
            ).pdf,
        )
    )
    letter = blocks_of(application, "cover_letter")
    if letter:
        contact, _ = _contact(user)
        body = f"{prose_text(letter)}\n\nSincerely,\n{name}"
        files.append(
            Attachment(
                f"{name} cover letter {safe_company}.pdf",
                "application/pdf",
                render_letter(sender=name, contact=contact, body=body, title="Cover letter"),
            )
        )
    uploads = (
        await db.execute(
            select(ApplicationFile).where(ApplicationFile.application_id == application.id)
        )
    ).scalars()
    wanted = {i["file_id"] for i in application.attachments_json if i["status"] == "uploaded"}
    files.extend(Attachment(f.filename, f.content_type, f.data) for f in uploads if f.id in wanted)

    # Check the status path before anything leaves, so a sent email is always recorded.
    try:
        applied = transition(transition(job.status, "approved"), "applied")
    except InvalidTransitionError as exc:
        raise WorkspaceError(str(exc)) from exc
    application.approved_at = now
    audit.record(
        db,
        user_id=user.id,
        actor="user",
        action="gate_approve",
        entity="application",
        entity_id=application.id,
    )
    try:
        message_id = await email.send(
            OutgoingEmail(
                to=application.recipient or "",
                subject=application.email_subject or f"Application for {job.title}",
                text=email_text(application, user),
                attachments=tuple(files),
                reply_to=user.email,
            )
        )
    except EmailError as exc:
        application.approved_at = None
        raise WorkspaceError(str(exc)) from exc

    mailer.record(db, user.id, "application", now)
    application.status = "sent"
    application.sent_at = now
    application.message_id = message_id[:300]
    application.follow_up_due = now + timedelta(days=FOLLOW_UP_DAYS)
    application.updated_at = now
    job.status = applied
    job.status_changed_at = now
    audit.record(
        db,
        user_id=user.id,
        actor="courier",
        action="application_sent",
        entity="application",
        entity_id=application.id,
        attachments=len(files),
    )
