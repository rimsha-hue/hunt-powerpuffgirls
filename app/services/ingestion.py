"""Builds the evidence ledger from a resume and from GitHub.

Resume flow: sniff the file, extract text, split it into numbered lines, then combine a
deterministic code pass with an optional LLM pass. Every LLM-proposed fact must cite a line
that actually supports it, or it is discarded. Blocklisted values never enter the ledger.
"""

from dataclasses import dataclass, field

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClaimBlocklist, Evidence, ResumeLine, User
from app.domain.ledger import (
    Fact,
    FactType,
    accept_proposed_facts,
    dedupe,
    extract_facts_by_code,
    normalize_fact,
)
from app.domain.normalize import normalize_text
from app.domain.resume import ContactInfo, ParsedLine, extract_contact, split_resume
from app.domain.taxonomy import Taxonomy
from app.domain.uploads import UploadRejectedError, sniff_resume
from app.integrations.github import GitHubClient, RepoInfo
from app.integrations.llm import LLMError, StructuredLLM
from app.integrations.resume_text import extract_text_async
from app.services import audit

logger = structlog.get_logger()

MIN_RESUME_LINES = 3

EXTRACTION_SYSTEM = (
    "You extract verifiable facts about a job candidate from their resume. The resume is given as "
    "numbered lines like [L12] text. Return atomic facts only: one skill, one employer, one role, "
    "one degree, one certification, one project or one quantified result per fact. Each fact must "
    "cite the single line number it comes from and use wording that appears on that line. Never "
    "infer, generalise or add anything that is not written on the cited line."
)
EXTRACTION_TASK = (
    "Extract the candidate's facts. Types: skill (a technology, tool or method), metric (a result "
    "with a number, quote the clause), employer (company or organisation name only), role (job "
    "title only), degree, certification, project (project name), personal (spoken languages, "
    "location, work authorisation). Skip contact details. At most 120 facts."
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


async def _blocklist(db: AsyncSession, user_id: int) -> set[str]:
    rows = await db.execute(
        select(ClaimBlocklist.normalized_value).where(ClaimBlocklist.user_id == user_id)
    )
    return set(rows.scalars())


def _numbered(lines: list[ParsedLine]) -> str:
    return "\n".join(f"[L{line.line_no}] ({line.section}) {line.text}" for line in lines)


async def _llm_facts(
    llm: StructuredLLM, lines: list[ParsedLine], taxonomy: Taxonomy
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
            data={"resume": _numbered(lines)},
        )
    except LLMError as exc:
        logger.warning("resume_llm_extraction_failed", error_type=type(exc).__name__)
        return [], 0, f"AI extraction was skipped: {exc} Facts came from deterministic parsing."
    kept, rejected = accept_proposed_facts(
        ((f.type, f.claim_text, f.line_id) for f in result.facts),
        {line.line_no: line for line in lines},
        taxonomy,
    )
    return kept, rejected, None


async def ingest_resume(
    db: AsyncSession,
    user: User,
    data: bytes,
    *,
    max_bytes: int,
    llm: StructuredLLM,
    taxonomy: Taxonomy,
) -> ResumeIngestion:
    kind = sniff_resume(data, max_bytes)
    lines = split_resume(await extract_text_async(data, kind))
    if len(lines) < MIN_RESUME_LINES:
        raise UploadRejectedError(
            "We couldn't find text in that file. If it's a scanned image, export a text-based PDF."
        )

    profile = user.profile
    version = profile.resume_version + 1

    # A new resume replaces the old resume-sourced facts. User confirmations and GitHub stay.
    await db.execute(delete(ResumeLine).where(ResumeLine.user_id == user.id))
    await db.execute(
        delete(Evidence).where(Evidence.user_id == user.id, Evidence.source == "resume")
    )

    rows = [
        ResumeLine(
            user_id=user.id,
            resume_version=version,
            section=l.section,
            line_no=l.line_no,
            text=l.text,
        )
        for l in lines  # noqa: E741
    ]
    db.add_all(rows)
    await db.flush()
    row_id = {row.line_no: row.id for row in rows}

    code_facts = extract_facts_by_code(lines, taxonomy)
    llm_facts, rejected, llm_note = await _llm_facts(llm, lines, taxonomy)
    blocked_values = await _blocklist(db, user.id)

    facts = dedupe([*code_facts, *llm_facts])
    kept = [f for f in facts if f.normalized_value not in blocked_values]
    evidence = [
        Evidence(
            user_id=user.id,
            claim_text=f.claim_text,
            type=f.type,
            normalized_value=f.normalized_value,
            source="resume",
            source_ref=str(row_id[f.line_no]),
            confirmed_by="system",
            confidence=1.0,
        )
        for f in kept
    ]
    db.add_all(evidence)

    contact = extract_contact(lines)
    profile.resume_version = version
    if contact.name and not user.name:
        user.name = contact.name
    if contact.phone and not profile.phone:
        profile.phone = contact.phone
    if contact.linkedin and not profile.linkedin:
        profile.linkedin = contact.linkedin
    if contact.github_username and not user.github_username:
        user.github_username = contact.github_username

    await db.flush()
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
        blocked=len(facts) - len(kept),
        llm_used=llm.configured and llm_note is None,
    )
    return ResumeIngestion(
        resume_version=version,
        line_count=len(lines),
        evidence=evidence,
        rejected_by_code=rejected,
        blocked=len(facts) - len(kept),
        llm_used=llm.configured and llm_note is None,
        llm_note=llm_note,
        contact=contact,
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
                Evidence(
                    user_id=user.id,
                    claim_text=claim_text,
                    type=fact_type,
                    normalized_value=value,
                    source="github",
                    source_ref=repo.html_url,
                    confirmed_by="system",
                    confidence=1.0,
                )
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
