"""Applying by form (Flow D): discover the questions, answer each by its type, let the user check
and approve every answer, then build the Apply Pack. Power Hunt never submits a form: the user
submits on the employer's site and says so (Gate 4).

Answers are stored on the application as a list of items (`answers_json`). A written answer
(descriptive or analytical) also keeps its sentence blocks, verified like the CV, under the
document id "q:<index>".
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Application, Claim, Evidence, Job, User
from app.domain.compose import prose_problems
from app.domain.cv import Block, plain
from app.domain.locations import LocationIndex
from app.domain.profile import LABELS
from app.domain.questions import (
    ProfileView,
    Question,
    classify,
    decline_option,
    pick_option,
    profile_answer,
    render_analytical,
    saved_answer_key,
    word_limit,
)
from app.domain.research import CompanyFact
from app.domain.status import InvalidTransitionError, transition
from app.domain.style import BANNED_PHRASES
from app.domain.taxonomy import Taxonomy
from app.integrations.embeddings import Similarity
from app.integrations.fetch import SafeFetcher
from app.integrations.forms import FoundForm, discover, parse_pasted
from app.integrations.llm import LLMError, StructuredLLM
from app.integrations.research import TavilyClient
from app.services import audit
from app.services.applications import (
    ANSWER_PREFIX,
    WorkspaceError,
    blocks_of,
    set_blocks,
    source_lines,
)
from app.services.composer import verify_prose
from app.services.researcher import company_facts, numbered

logger = structlog.get_logger()

FOLLOW_UP_DAYS = 7
MAX_ANSWER = 4000
WRITTEN = frozenset({"descriptive", "analytical"})
# Never remembered across applications: each employer's agreement is its own decision.
NOT_REMEMBERED = frozenset({"consent", "file"})

_RULES = (
    "Use only facts from the approved CV for anything about the candidate; never add a skill, "
    "number, employer, degree or result it doesn't state. Plain, specific, warm; vary sentence "
    "length. No em or en dashes. Never use: " + ", ".join(BANNED_PHRASES) + "."
)
DESCRIPTIVE_SYSTEM = (
    "You answer one question on a job application form, in the candidate's voice. " + _RULES + " "
    "Use exactly one company fact from the numbered list, in your own words, and give its number "
    "in fact. Connect it to something concrete the candidate has done."
)
ANALYTICAL_SYSTEM = (
    "You answer an analytical question on a job application form, in the candidate's voice. "
    + _RULES
    + " State your assumptions, give an approach in two to four steps, the result, and one risk "
    "or caveat. Use numbers only from the question, the company facts or the approved CV."
)


class DescriptiveAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=MAX_ANSWER)
    fact: int | None = Field(default=None, ge=1)


class AnalyticalAnswer(BaseModel):
    assumptions: list[str] = Field(min_length=1, max_length=4)
    steps: list[str] = Field(min_length=2, max_length=4)
    result: str = Field(min_length=1, max_length=800)
    risk: str = Field(min_length=1, max_length=500)


def _question(item: dict[str, Any]) -> Question:
    return Question(
        label=item["label"],
        field_type=item["field_type"],
        required=item["required"],
        description=item.get("description", ""),
        options=tuple(item.get("options") or ()),
        name=item.get("name", ""),
    )


def _items(questions: Sequence[Question]) -> list[dict[str, Any]]:
    items = []
    for question in questions:
        kind, key = classify(question)
        items.append(
            {
                **question.to_json(),
                "type": kind,
                "key": key,
                "answer": "",
                "status": "needs_you",
                "source": None,
                "company_fact": None,
                "note": None,
                "blocks": [],
            }
        )
    return items


def _require_open(application: Application) -> None:
    if application.status == "draft":
        raise WorkspaceError("Approve your CV first; the answers are written from it.")
    if application.sent_at is not None:
        raise WorkspaceError("This application was already sent or submitted.")


async def _reset(db: AsyncSession, application: Application, found: FoundForm) -> None:
    await db.execute(
        delete(Claim).where(
            Claim.application_id == application.id, Claim.document.like(f"{ANSWER_PREFIX}%")
        )
    )
    application.form_source = found.source
    application.form_url = found.url
    application.answers_json = _items(found.questions)


async def discover_questions(
    db: AsyncSession,
    application: Application,
    job: Job,
    *,
    http: Any,
    fetcher: SafeFetcher,
    now: datetime,
) -> None:
    """Spec Flow D step 1. Raises FormDiscoveryError (safe to show) when nothing is readable."""
    _require_open(application)
    urls = list(dict.fromkeys(u for u in (job.apply_url, job.url) if u))
    found = await discover(urls, http=http, fetcher=fetcher)
    await _reset(db, application, found)
    application.updated_at = now
    audit.record(
        db,
        user_id=application.user_id,
        actor="composer",
        action="form_discovered",
        entity="application",
        entity_id=application.id,
        source=found.source,
        questions=len(found.questions),
    )


async def paste_questions(
    db: AsyncSession, application: Application, job: Job, text: str, *, now: datetime
) -> None:
    _require_open(application)
    questions = parse_pasted(text)
    if not questions:
        raise WorkspaceError("Paste the questions one per line.")
    await _reset(db, application, FoundForm("pasted", job.apply_url or job.url, questions))
    application.updated_at = now


def profile_view(user: User, locations: LocationIndex) -> ProfileView:
    profile = user.profile
    name = user.name.strip()
    first, _, last = name.partition(" ")
    place = locations.get(profile.location) if profile.location else None
    salary = ""
    if profile.salary_amount and profile.salary_currency and profile.salary_period:
        period = LABELS["salary_period"].get(profile.salary_period, "")
        salary = f"{profile.salary_amount:,} {profile.salary_currency} {period}".strip()
    return ProfileView(
        first_name=first,
        last_name=last,
        full_name=name,
        email=user.email,
        phone=profile.phone or "",
        linkedin=profile.linkedin or "",
        github=f"https://github.com/{user.github_username}" if user.github_username else "",
        # A website field takes the portfolio, else the GitHub profile.
        website=user.portfolio_url
        or (f"https://github.com/{user.github_username}" if user.github_username else ""),
        location=place.label if place else "",
        work_authorization=profile.work_authorization or "",
        work_authorization_label=LABELS["work_authorization"].get(
            profile.work_authorization or "", ""
        ),
        notice_period=LABELS["notice_period"].get(profile.notice_period or "", ""),
        salary=salary,
        saved={str(k): str(v) for k, v in (profile.saved_answers or {}).items()},
    )


def _ledger_answer(key: str | None, evidence: Sequence[Evidence]) -> tuple[str, Evidence] | None:
    if key == "degree":
        rows = [e for e in evidence if e.type == "degree"]
    elif key == "gpa":
        rows = [e for e in evidence if e.type == "metric" and "gpa/" in e.normalized_value]
    else:
        rows = []
    rows.sort(key=lambda e: e.source != "resume")
    return (rows[0].claim_text, rows[0]) if rows else None


async def _written(
    item: dict[str, Any],
    *,
    job: Job,
    cv: Sequence[Block],
    facts: Sequence[CompanyFact],
    llm: StructuredLLM,
) -> tuple[str, CompanyFact | None, str | None]:
    """(answer, cited company fact, note) for a descriptive or analytical question."""
    question = _question(item)
    low, high, max_chars = word_limit(question)
    limit = f"{low} to {high} words" + (f", at most {max_chars} characters" if max_chars else "")
    data = {
        "job": f"{job.title} at {job.company}",
        "question": f"{question.label}\n{question.description}".strip(),
        "approved_cv": "\n".join(b.text for b in cv if b.text),
        "company_facts": numbered(facts) or "(none)",
    }
    task = f"Answer the question in {limit}."
    try:
        if item["type"] == "analytical":
            analytical = await llm.generate(
                schema=AnalyticalAnswer, system=ANALYTICAL_SYSTEM, task=task, data=data
            )
            text = render_analytical(
                analytical.assumptions, analytical.steps, analytical.result, analytical.risk
            )
            return plain(text), None, None
        answer = await llm.generate(
            schema=DescriptiveAnswer, system=DESCRIPTIVE_SYSTEM, task=task, data=data
        )
        problems = prose_problems(answer.answer, low, high)
        cited = facts[answer.fact - 1] if answer.fact and answer.fact <= len(facts) else None
        if facts and cited is None:
            problems.append("cites no company fact")
        if problems:  # one rewrite, then the user decides (spec section 8)
            answer = await llm.generate(
                schema=DescriptiveAnswer,
                system=DESCRIPTIVE_SYSTEM,
                task=f"{task} Your last answer {'; '.join(problems)}. Fix that.",
                data={**data, "last_answer": answer.answer},
            )
            cited = facts[answer.fact - 1] if answer.fact and answer.fact <= len(facts) else None
            problems = prose_problems(answer.answer, low, high)
            if facts and cited is None:
                problems.append("cites no company fact")
    except LLMError as exc:
        logger.warning("answer_llm_failed", error_type=type(exc).__name__)
        return (
            "",
            None,
            f"AI writing failed ({exc}) Write this answer yourself; it is checked the same way.",
        )
    note = f"This answer {'; '.join(problems)}. Edit it before approving." if problems else None
    return plain(answer.answer.strip()), cited, note


def _source(label: str, detail: str | None = None, url: str | None = None) -> dict[str, Any]:
    return {"label": label, "detail": detail, "url": url}


async def _verify_answer(
    db: AsyncSession,
    application: Application,
    index: int,
    text: str,
    *,
    job: Job,
    context: Sequence[str],
    llm: StructuredLLM,
    taxonomy: Taxonomy,
    similarity: Similarity,
    threshold: float,
) -> int:
    lines = await source_lines(db, application.user_id)
    return await verify_prose(
        db,
        application,
        f"{ANSWER_PREFIX}{index}",
        text,
        lines=lines,
        llm=llm,
        taxonomy=taxonomy,
        similarity=similarity,
        threshold=threshold,
        ignore=(job.title, job.company),
        context=context,
        subject=job.company,
    )


async def draft_answers(
    db: AsyncSession,
    user: User,
    job: Job,
    application: Application,
    *,
    llm: StructuredLLM,
    taxonomy: Taxonomy,
    similarity: Similarity,
    threshold: float,
    locations: LocationIndex,
    tavily: TavilyClient | None,
    now: datetime,
) -> str | None:
    """Answers every question that isn't approved yet. Returns the research note, if any."""
    _require_open(application)
    if not application.answers_json:
        raise WorkspaceError("Find the form's questions first.")
    view = profile_view(user, locations)
    evidence = list(
        (await db.execute(select(Evidence).where(Evidence.user_id == user.id))).scalars()
    )
    cv = blocks_of(application)
    research = None
    if any(i["type"] in WRITTEN for i in application.answers_json):
        research = await company_facts(
            db,
            company=job.company,
            job_description=job.description,
            job_url=job.url,
            tavily=tavily,
            now=now,
        )
    facts = research.facts if research else []

    items = [dict(item) for item in application.answers_json]
    written: list[tuple[int, str, list[str]]] = []
    for index, item in enumerate(items):
        if item["status"] == "approved":
            continue
        question = _question(item)
        kind, key = item["type"], item["key"]
        item.update(
            answer="", status="needs_you", source=None, company_fact=None, note=None, blocks=[]
        )
        if kind == "file":
            item.update(
                answer="Tailored CV (PDF)" if key == "cv" else "Cover letter (PDF)",
                status="ready",
                source=_source("Apply Pack file"),
            )
            if key == "cover_letter" and not application.cover_letter_blocks_json:
                item.update(
                    answer="",
                    status="needs_you",
                    note="Draft the email first to get a cover letter, or attach your own.",
                )
        elif kind == "consent":
            item["note"] = (
                "A legal agreement or policy. Read it on the form and answer it yourself."
            )
        elif kind == "voluntary":
            decline = decline_option(question.options)
            if decline:
                item.update(
                    answer=decline,
                    status="ready",
                    source=_source("Voluntary", "Not shared by default. Change it if you want to."),
                )
            else:
                item["note"] = "Voluntary. Answer only if you want to."
        elif kind == "factual_ledger":
            found = _ledger_answer(key, evidence)
            if found:
                text, row = found
                item.update(
                    answer=text,
                    status="ready",
                    source=_source("Your ledger", f"{row.type}: {row.claim_text}"),
                )
            else:
                item["note"] = "Your ledger doesn't show this. Answer once and it is remembered."
        elif kind == "factual_profile":
            saved = view.saved.get(saved_answer_key(question.label))
            answer = (
                pick_option(saved, question.options)
                if saved
                else profile_answer(key, question, view)
            )
            if answer:
                label = "Saved answer" if saved else "Your profile"
                item.update(answer=answer, status="ready", source=_source(label))
            else:
                item["note"] = "Answer once and Power Hunt remembers it."
        else:
            text, cited, note = (
                await _written(item, job=job, cv=cv, facts=facts, llm=llm)
                if llm.configured
                else (
                    "",
                    None,
                    "AI writing is off. Write this answer; it is checked like your CV.",
                )
            )
            item.update(answer=text, note=note)
            if cited:
                item["company_fact"] = {"fact": cited.fact, "url": cited.source_url}
            if text:
                context = [f"{question.label} {question.description}", *(f.fact for f in facts)]
                written.append((index, text, context))
    application.answers_json = items

    for index, text, context in written:
        open_claims = await _verify_answer(
            db,
            application,
            index,
            text,
            job=job,
            context=context,
            llm=llm,
            taxonomy=taxonomy,
            similarity=similarity,
            threshold=threshold,
        )
        items = [dict(i) for i in application.answers_json]
        items[index]["status"] = "needs_you" if open_claims else "ready"
        application.answers_json = items
        application.questions_asked += open_claims
    application.updated_at = now
    audit.record(
        db,
        user_id=user.id,
        actor="composer",
        action="form_answered",
        entity="application",
        entity_id=application.id,
        questions=len(items),
        needs_you=sum(1 for i in application.answers_json if i["status"] == "needs_you"),
    )
    return research.note if research else None


async def edit_answer(
    db: AsyncSession,
    user: User,
    job: Job,
    application: Application,
    index: int,
    text: str,
    *,
    remember: bool,
    llm: StructuredLLM,
    taxonomy: Taxonomy,
    similarity: Similarity,
    threshold: float,
    now: datetime,
) -> None:
    """The user's own answer. Written answers are verified again; short ones must fit the
    question's options, and are remembered in the profile when the user asks (never consent)."""
    _require_open(application)
    if not 0 <= index < len(application.answers_json):
        raise WorkspaceError("Question not found.")
    item = dict(application.answers_json[index])
    question = _question(item)
    text = plain(text.strip())[:MAX_ANSWER]
    if item["type"] in WRITTEN:
        await db.execute(
            delete(Claim).where(
                Claim.application_id == application.id, Claim.document == f"{ANSWER_PREFIX}{index}"
            )
        )
        if text:
            context = [f"{question.label} {question.description}"]
            if item.get("company_fact"):
                context.append(item["company_fact"]["fact"])
            open_claims = await _verify_answer(
                db,
                application,
                index,
                text,
                job=job,
                context=context,
                llm=llm,
                taxonomy=taxonomy,
                similarity=similarity,
                threshold=threshold,
            )
        else:
            set_blocks(application, f"{ANSWER_PREFIX}{index}", [])
            open_claims = 0
        item = dict(application.answers_json[index])
        item.update(
            status="needs_you" if open_claims or not text else "ready",
            source=_source("Written by you") if text else None,
            note=None,
        )
    else:
        if question.options and text:
            chosen = pick_option(text, question.options)
            if chosen is None:
                raise WorkspaceError("Pick one of the form's options.")
            text = chosen
        item.update(
            answer=text,
            status="ready" if text else "needs_you",
            source=_source("Your answer") if text else None,
            note=None,
        )
        if remember and text and item["type"] not in NOT_REMEMBERED:
            profile = user.profile
            profile.saved_answers = {
                **(profile.saved_answers or {}),
                saved_answer_key(question.label): text,
            }
    items = [dict(i) for i in application.answers_json]
    items[index] = item
    application.answers_json = items
    application.updated_at = now


def sync_status(application: Application, document: str, claims: Sequence[Claim]) -> None:
    """After the user answers a claim inside a written answer, its status follows."""
    index = int(document[len(ANSWER_PREFIX) :])
    items = [dict(i) for i in application.answers_json]
    still_open = any(c.bucket == "needs_you" for c in claims)
    if items[index]["status"] != "approved":
        items[index]["status"] = (
            "needs_you" if still_open or not items[index]["answer"] else "ready"
        )
    application.answers_json = items


async def approve_answer(
    db: AsyncSession, application: Application, index: int, now: datetime
) -> None:
    """The user checked this answer (spec Flow D step 4)."""
    _require_open(application)
    if not 0 <= index < len(application.answers_json):
        raise WorkspaceError("Question not found.")
    item = dict(application.answers_json[index])
    open_claims = (
        await db.execute(
            select(Claim.id).where(
                Claim.application_id == application.id,
                Claim.document == f"{ANSWER_PREFIX}{index}",
                Claim.bucket == "needs_you",
            )
        )
    ).all()
    if open_claims:
        raise WorkspaceError("Answer the claims in this answer that need you first.")
    if not item["answer"] and item["required"]:
        raise WorkspaceError("This question is required. Write an answer first.")
    item["status"] = "approved"
    items = [dict(i) for i in application.answers_json]
    items[index] = item
    application.answers_json = items
    application.updated_at = now
    audit.record(
        db,
        user_id=application.user_id,
        actor="user",
        action="answer_approved",
        entity="application",
        entity_id=application.id,
        question=index,
    )


def pack_blockers(application: Application) -> list[str]:
    """Why the Apply Pack isn't final yet (empty when it is)."""
    if not application.answers_json:
        return ["Find the form's questions."]
    waiting = [i for i in application.answers_json if i["status"] != "approved"]
    return [f"Check and approve {len(waiting)} answer(s)."] if waiting else []


async def mark_submitted(
    db: AsyncSession, job: Job, application: Application, now: datetime
) -> None:
    """Gate 4: the user submitted on the employer's site. Power Hunt never submits a form."""
    _require_open(application)
    reasons = pack_blockers(application)
    if reasons:
        raise WorkspaceError(" ".join(reasons))
    try:
        applied = transition(transition(job.status, "approved"), "applied")
    except InvalidTransitionError as exc:
        raise WorkspaceError(str(exc)) from exc
    application.status = "submitted"
    application.approved_at = now
    application.sent_at = now
    application.follow_up_due = now + timedelta(days=FOLLOW_UP_DAYS)
    application.updated_at = now
    job.status = applied
    job.status_changed_at = now
    audit.record(
        db,
        user_id=application.user_id,
        actor="user",
        action="gate_submit",
        entity="application",
        entity_id=application.id,
    )
