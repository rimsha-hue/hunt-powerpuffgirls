"""Builds the evidence ledger from a resume, a portfolio page and GitHub.

Document flow (resume or portfolio): extract text, split it into numbered logical lines, then
combine a deterministic code pass with an optional LLM pass. Every LLM-proposed fact must cite a
line that actually supports it, or it is discarded. Blocklisted values never enter the ledger.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClaimBlocklist, Evidence, ResumeLine, User
from app.domain.contact import normalize_linkedin, normalize_person_name, normalize_phone
from app.domain.ledger import (
    EvidenceSource,
    Fact,
    FactType,
    accept_proposed_facts,
    dedupe,
    extract_facts_by_code,
    normalize_fact,
)
from app.domain.normalize import normalize_text
from app.domain.profile import public_url
from app.domain.resume import ContactInfo, ParsedLine, extract_contact, split_resume
from app.domain.taxonomy import Taxonomy
from app.domain.uploads import UploadRejectedError, sniff_resume
from app.integrations.fetch import FetchError, SafeFetcher
from app.integrations.github import USERNAME_RE, GitHubClient, RepoInfo
from app.integrations.llm import LLMError, StructuredLLM
from app.integrations.resume_text import extract_text_async
from app.integrations.webtext import html_to_text, markdown_to_text
from app.services import audit

logger = structlog.get_logger()

MIN_RESUME_LINES = 3
# Below this much readable text, a page is probably rendered by JavaScript; ask Jina Reader.
MIN_PAGE_TEXT = 300

# Code accepts a proposed fact only when every word and number in claim_text appears on the cited
# line (domain/ledger.py is_supported_by_line), so the prompt asks for verbatim copies.
EXTRACTION_SYSTEM = (
    "You extract verifiable facts about a job candidate from a document they wrote (a resume or a "
    "portfolio page). Each line is given as [L12] (section) text: L12 is the line number and the "
    "section in brackets is a label we added, not part of the text. Return atomic facts: one "
    "skill, employer, role, degree, certification, project or quantified result per fact. Each "
    "fact cites the one line it comes from in line_id, and its claim_text is copied word for word "
    "from that line. Never paraphrase, expand abbreviations, combine lines, or add a word or number "
    "that is not on the cited line. Code checks every fact against its line and discards any "
    "that do not match."
)
EXTRACTION_TASK = (
    "List every fact the document states, line by line, covering all sections. Types:\n"
    "- skill: one language, framework, library, tool, platform or method per fact, as written "
    '(a line "Python, SQL, Power BI" gives three skills).\n'
    "- role: the job title only, without employer or dates.\n"
    "- employer: the company or organisation name only, without location or dates.\n"
    "- degree: the degree as written, from education lines only. Skip degrees the candidate only "
    "plans to pursue.\n"
    "- certification: the certificate or course name as written.\n"
    "- project: the project name only.\n"
    "- metric: the clause that states a result with a number, for example "
    '"cut report time by 40%". Keep the number and its unit exactly as written.\n'
    "- personal: spoken languages, location and work authorisation.\n"
    "Skip contact details (name, email, phone, links). At most 120 facts."
)


class ProposedFact(BaseModel):
    claim_text: str = Field(min_length=1, max_length=300)
    type: FactType
    line_id: int = Field(ge=1)


class ExtractionResult(BaseModel):
    facts: list[ProposedFact] = Field(default_factory=list, max_length=200)


@dataclass
class ResumeIngestion:
    resume_version: int
    line_count: int
    evidence: list[Evidence]
    rejected_by_code: int
    blocked: int
    llm_used: bool
    llm_note: str | None
    contact: ContactInfo


@dataclass
class GitHubIngestion:
    username: str
    repos_scanned: int
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class PortfolioIngestion:
    url: str
    method: Literal["direct", "rendered"]
    line_count: int
    evidence: list[Evidence]
    rejected_by_code: int
    llm_note: str | None


async def _blocklist(db: AsyncSession, user_id: int) -> set[str]:
    rows = await db.execute(
        select(ClaimBlocklist.normalized_value).where(ClaimBlocklist.user_id == user_id)
    )
    return set(rows.scalars())


def _numbered(lines: list[ParsedLine]) -> str:
    return "\n".join(f"[L{line.line_no}] ({line.section}) {line.text}" for line in lines)


def _evidence(user_id: int, fact: Fact, source: EvidenceSource, source_ref: str) -> Evidence:
    return Evidence(
        user_id=user_id,
        claim_text=fact.claim_text,
        type=fact.type,
        normalized_value=fact.normalized_value,
        source=source,
        source_ref=source_ref,
        context=fact.context,
        confirmed_by="system",
        confidence=1.0,
    )


async def _llm_facts(
    llm: StructuredLLM, lines: list[ParsedLine], taxonomy: Taxonomy, document: str
) -> tuple[list[Fact], int, str | None]:
    if not llm.configured:
        return (
            [],
            0,
            "AI extraction is off (no LLM key configured); facts came from deterministic parsing.",
        )
    try:
        result = await llm.generate(
            schema=ExtractionResult,
            system=EXTRACTION_SYSTEM,
            task=EXTRACTION_TASK,
            data={document: _numbered(lines)},
        )
    except LLMError as exc:
        logger.warning("llm_extraction_failed", document=document, error_type=type(exc).__name__)
        return [], 0, f"AI extraction was skipped: {exc} Facts came from deterministic parsing."
    kept, rejected = accept_proposed_facts(
        ((f.type, f.claim_text, f.line_id) for f in result.facts),
        {line.line_no: line for line in lines},
        taxonomy,
    )
    return kept, rejected, None


async def _document_facts(
    db: AsyncSession,
    user: User,
    lines: list[ParsedLine],
    llm: StructuredLLM,
    taxonomy: Taxonomy,
    document: str,
) -> tuple[list[Fact], int, int, str | None]:
    """(kept facts, rejected by code, blocked by user, llm note) for a numbered document."""
    code_facts = extract_facts_by_code(lines, taxonomy)
    llm_facts, rejected, llm_note = await _llm_facts(llm, lines, taxonomy, document)
    blocked_values = await _blocklist(db, user.id)
    facts = dedupe([*code_facts, *llm_facts])
    kept = [f for f in facts if f.normalized_value not in blocked_values]
    return kept, rejected, len(facts) - len(kept), llm_note


def _prefill_from_contact(user: User, contact: ContactInfo, phone_region: str) -> None:
    """Fill empty profile fields from the resume header, through the same validators the profile
    form uses. A value that fails validation is skipped, never stored half-right."""
    profile = user.profile
    candidates: list[tuple[str, object, str | None, Callable[[str], str]]] = [
        ("name", user, contact.name, normalize_person_name),
        ("phone", profile, contact.phone, lambda v: normalize_phone(v, phone_region)),
        ("linkedin", profile, contact.linkedin, normalize_linkedin),
        ("github_username", user, contact.github_username, _github_username),
        ("portfolio_url", user, contact.portfolio_url, public_url),
    ]
    for field_name, target, raw, normalize in candidates:
        if not raw or getattr(target, field_name):
            continue
        try:
            setattr(target, field_name, normalize(raw))
        except ValueError:
            logger.info("resume_contact_skipped", field=field_name)


def _github_username(value: str) -> str:
    if not USERNAME_RE.match(value):
        raise ValueError("not a GitHub username")
    return value


async def ingest_resume(
    db: AsyncSession,
    user: User,
    data: bytes,
    *,
    max_bytes: int,
    llm: StructuredLLM,
    taxonomy: Taxonomy,
    phone_region: str,
) -> ResumeIngestion:
    kind = sniff_resume(data, max_bytes)
    lines = split_resume(await extract_text_async(data, kind))
    if len(lines) < MIN_RESUME_LINES:
        raise UploadRejectedError(
            "We couldn't find text in that file. If it's a scanned image, export a text-based PDF."
        )

    profile = user.profile
    version = profile.resume_version + 1

    # A new resume replaces the old resume-sourced facts. Confirmations, GitHub and portfolio stay.
    await db.execute(delete(ResumeLine).where(ResumeLine.user_id == user.id))
    await db.execute(
        delete(Evidence).where(Evidence.user_id == user.id, Evidence.source == "resume")
    )

    rows = [
        ResumeLine(
            user_id=user.id,
            resume_version=version,
            section=ln.section,
            line_no=ln.line_no,
            text=ln.text,
            bulleted=ln.bulleted,
        )
        for ln in lines
    ]
    db.add_all(rows)
    await db.flush()
    row_id = {row.line_no: row.id for row in rows}

    kept, rejected, blocked, llm_note = await _document_facts(
        db, user, lines, llm, taxonomy, "resume"
    )
    evidence = [_evidence(user.id, f, "resume", str(row_id[f.line_no])) for f in kept]
    db.add_all(evidence)

    contact = extract_contact(lines)
    profile.resume_version = version
    _prefill_from_contact(user, contact, phone_region)

    await db.flush()
    llm_used = llm.configured and llm_note is None
    audit.record(
        db,
        user_id=user.id,
        actor="ledger",
        action="resume_ingested",
        entity="resume",
        entity_id=version,
        lines=len(lines),
        facts=len(evidence),
        rejected_by_code=rejected,
        blocked=blocked,
        llm_used=llm_used,
    )
    return ResumeIngestion(
        resume_version=version,
        line_count=len(lines),
        evidence=evidence,
        rejected_by_code=rejected,
        blocked=blocked,
        llm_used=llm_used,
        llm_note=llm_note,
        contact=contact,
    )


async def fetch_page_text(
    fetcher: SafeFetcher, url: str, renderer_url: str
) -> tuple[str, Literal["direct", "rendered"]]:
    """Readable text of a public page.

    Pages that render with JavaScript have little text in their HTML; for those the page is read
    through a rendering service (Jina Reader) at `renderer_url`. An empty `renderer_url` disables it.
    """
    page = await fetcher.fetch(url)
    # Some hosts serve bots Markdown or plain text; both go through the Markdown cleaner.
    text = html_to_text(page.text) if "html" in page.content_type else markdown_to_text(page.text)
    if len(text) >= MIN_PAGE_TEXT or not renderer_url:
        return text, "direct"
    try:
        # The target was already checked as public by the direct fetch above.
        rendered = await fetcher.fetch(f"{renderer_url}{page.url}")
    except FetchError:
        if text:
            return text, "direct"
        raise
    rendered_text = markdown_to_text(rendered.text)
    if len(rendered_text) > len(text):
        return rendered_text, "rendered"
    return text, "direct"


async def ingest_portfolio(
    db: AsyncSession,
    user: User,
    fetcher: SafeFetcher,
    llm: StructuredLLM,
    taxonomy: Taxonomy,
    *,
    renderer_url: str,
) -> PortfolioIngestion:
    if not user.portfolio_url:
        raise ValueError("No portfolio URL on the profile")
    text, method = await fetch_page_text(fetcher, user.portfolio_url, renderer_url)
    lines = split_resume(text)

    await db.execute(
        delete(Evidence).where(Evidence.user_id == user.id, Evidence.source == "portfolio")
    )
    kept, rejected, blocked, llm_note = await _document_facts(
        db, user, lines, llm, taxonomy, "portfolio"
    )
    evidence = [_evidence(user.id, f, "portfolio", user.portfolio_url) for f in kept]
    db.add_all(evidence)
    await db.flush()
    audit.record(
        db,
        user_id=user.id,
        actor="ledger",
        action="portfolio_ingested",
        entity="portfolio",
        entity_id=None,
        method=method,
        lines=len(lines),
        facts=len(evidence),
        rejected_by_code=rejected,
        blocked=blocked,
    )
    return PortfolioIngestion(
        url=user.portfolio_url,
        method=method,
        line_count=len(lines),
        evidence=evidence,
        rejected_by_code=rejected,
        llm_note=llm_note,
    )


def _repo_facts(repo: RepoInfo, taxonomy: Taxonomy) -> list[tuple[FactType, str, str]]:
    """(type, claim_text, normalized_value) facts a public repo proves."""
    facts: list[tuple[FactType, str, str]] = []
    terms = {*(repo.languages or {}), *([repo.language] if repo.language else []), *repo.topics}
    for term in terms:
        canonical = taxonomy.canonical(term.replace("-", " ")) or taxonomy.canonical(term)
        if canonical:  # unknown topics ("hacktoberfest") are not skills
            facts.append(("skill", canonical, canonical.lower()))
    project_value = normalize_fact(
        "project", repo.name.replace("-", " ").replace("_", " "), taxonomy
    )
    if project_value:
        label = f"{repo.name}: {repo.description}" if repo.description else repo.name
        facts.append(("project", label[:300], project_value))
    return facts


async def ingest_github(
    db: AsyncSession, user: User, client: GitHubClient, taxonomy: Taxonomy
) -> GitHubIngestion:
    if not user.github_username:
        raise ValueError("No GitHub username on the profile")
    repos = await client.list_repos(user.github_username)

    existing = await db.execute(
        select(Evidence.type, Evidence.normalized_value).where(
            Evidence.user_id == user.id, Evidence.source == "github"
        )
    )
    seen = {(t, v) for t, v in existing.all()}
    blocked_values = await _blocklist(db, user.id)

    report = GitHubIngestion(username=user.github_username, repos_scanned=len(repos))
    for repo in repos:
        for fact_type, claim_text, value in _repo_facts(repo, taxonomy):
            if (fact_type, value) in seen or value in blocked_values:
                continue
            seen.add((fact_type, value))
            report.evidence.append(
                _evidence(user.id, Fact(claim_text, fact_type, value, 0), "github", repo.html_url)
            )
    db.add_all(report.evidence)
    await db.flush()
    audit.record(
        db,
        user_id=user.id,
        actor="ledger",
        action="github_ingested",
        entity="github",
        entity_id=normalize_text(user.github_username),
        repos=len(repos),
        facts=len(report.evidence),
    )
    return report
