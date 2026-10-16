"""Verifier: finds every claim in the tailored CV and routes it (domain/claims.py), and applies the
user's answers to Needs-you claims (Gate 2).

Claims on the user's blocklist are removed from the text automatically. Confirming a claim adds it
to the ledger as user-confirmed evidence, so the same claim is Verified on the next application.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Claim, ClaimBlocklist, Evidence
from app.domain.claims import FoundClaim, Ledger, Routing, code_claims, locate, merge, route
from app.domain.cv import Block, replace_span, too_short
from app.domain.ledger import FactType
from app.domain.taxonomy import Taxonomy
from app.integrations.embeddings import Similarity
from app.integrations.llm import LLMError, StructuredLLM
from app.services import audit

logger = structlog.get_logger()

MAX_NOTE = 300

CLAIM_SYSTEM = (
    "You list the claims a CV makes about the candidate. Bullets are given as [B4] text. A claim "
    "is one checkable statement: a skill or tool used, a result, a role or title held, an "
    "employer, a degree, a certification, a project, or an action taken. Quote each claim exactly "
    "as it appears in its bullet, as a short span (a few words), and give its type. Do not "
    "paraphrase, merge bullets or add anything."
)
CLAIM_TASK = "List every claim in every bullet. Types: skill, metric, employer, degree, certification, project, role, personal."


class ProposedClaim(BaseModel):
    block: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=300)
    type: FactType


class ClaimList(BaseModel):
    claims: list[ProposedClaim] = Field(default_factory=list, max_length=300)


class DecisionError(Exception):
    """Safe to show to the user."""


@dataclass
class Draft:
    block_index: int
    claim: FoundClaim
    bucket: str
    normalized: str
    similarity: float
    line_id: int


@dataclass
class Verification:
    blocks: list[Block]
    drafts: list[Draft]
    note: str | None


async def load_ledger(db: AsyncSession, user_id: int) -> tuple[Ledger, list[str]]:
    """(ledger keys incl. blocklist, employer names as written) for one user."""
    rows = (
        await db.execute(
            select(Evidence.type, Evidence.normalized_value, Evidence.claim_text).where(
                Evidence.user_id == user_id
            )
        )
    ).all()
    blocked = (
        await db.execute(
            select(ClaimBlocklist.normalized_value).where(ClaimBlocklist.user_id == user_id)
        )
    ).scalars()
    ledger = Ledger.build(((t, v) for t, v, _ in rows), blocked)
    employers = sorted({text for t, _, text in rows if t == "employer"}, key=len, reverse=True)
    return ledger, employers


async def _llm_claims(
    blocks: Sequence[Block], indexes: Sequence[int], llm: StructuredLLM
) -> tuple[list[tuple[int, FoundClaim]], str | None]:
    targets = [i for i in indexes if blocks[i].rewritten and blocks[i].text]
    if not targets or not llm.configured:
        return [], None
    try:
        result = await llm.generate(
            schema=ClaimList,
            system=CLAIM_SYSTEM,
            task=CLAIM_TASK,
            data={"cv_bullets": "\n".join(f"[B{i}] {blocks[i].text}" for i in targets)},
        )
    except LLMError as exc:
        logger.warning("verifier_llm_failed", error_type=type(exc).__name__)
        return (
            [],
            f"The AI claim check was skipped ({exc}) Numbers, skills, degrees and employers were still checked by code.",
        )
    found: list[tuple[int, FoundClaim]] = []
    for item in result.claims:
        if item.block not in targets:
            continue
        span = locate(blocks[item.block].text, item.text)
        if span:
            text = blocks[item.block].text[span[0] : span[1]]
            found.append((item.block, FoundClaim(text, item.type, span[0], span[1], "llm")))
    return found, None


def _route_block(
    block: Block,
    llm_found: Sequence[FoundClaim],
    sim: float,
    *,
    ledger: Ledger,
    employers: Sequence[str],
    taxonomy: Taxonomy,
    threshold: float,
) -> list[tuple[FoundClaim, Routing]]:
    found = merge([*code_claims(block.text, taxonomy, employers), *llm_found])
    return [
        (
            claim,
            route(
                claim,
                ledger=ledger,
                taxonomy=taxonomy,
                source_line=block.original,
                similarity=sim,
                threshold=threshold,
            ),
        )
        for claim in found
    ]


async def verify(
    blocks: Sequence[Block],
    *,
    ledger: Ledger,
    employers: Sequence[str],
    taxonomy: Taxonomy,
    llm: StructuredLLM,
    similarity: Similarity,
    threshold: float,
) -> Verification:
    """Claims for every bullet. Blocked claims are cut from the text before anything is stored."""
    out = list(blocks)
    indexes = [i for i, b in enumerate(out) if b.kind == "bullet" and b.text]
    llm_found, note = await _llm_claims(out, indexes, llm)
    pairs = [(out[i].text, out[i].original) for i in indexes]
    sims = dict(zip(indexes, await similarity.scores(pairs), strict=True))
    options = {
        "ledger": ledger,
        "employers": employers,
        "taxonomy": taxonomy,
        "threshold": threshold,
    }
    drafts: list[Draft] = []
    for i in indexes:
        block = out[i]
        sim = 1.0 if not block.rewritten else round(sims[i], 4)
        routed = _route_block(block, [c for b, c in llm_found if b == i], sim, **options)  # type: ignore[arg-type]
        blocked = [(c, r) for c, r in routed if r.bucket == "blocked"]
        if blocked:
            text = block.text
            for claim, _ in sorted(blocked, key=lambda pair: -pair[0].start):
                text = replace_span(text, claim.start, claim.end, "")
            block = out[i] = replace(block, text="" if too_short(text) else text)
            drafts.extend(
                Draft(i, replace(c, start=0, end=0), "blocked", r.normalized, sim, block.line_id)
                for c, r in blocked
            )
            # The wording changed, so the rest is found again by code on the new text.
            routed = [
                (c, r)
                for c, r in (_route_block(block, [], sim, **options) if block.text else [])  # type: ignore[arg-type]
                if r.bucket != "blocked"
            ]
        drafts.extend(Draft(i, c, r.bucket, r.normalized, sim, block.line_id) for c, r in routed)
    return Verification(out, drafts, note)


def claim_rows(application_id: int, user_id: int, drafts: Sequence[Draft]) -> list[Claim]:
    return [
        Claim(
            application_id=application_id,
            user_id=user_id,
            block_index=d.block_index,
            text=d.claim.text[:500],
            type=d.claim.type,
            normalized_value=d.normalized[:500],
            span_start=d.claim.start,
            span_end=d.claim.end,
            bucket=d.bucket,
            origin=d.claim.origin,
            cited_line_id=d.line_id,
            similarity=d.similarity,
        )
        for d in drafts
    ]


# ---- Gate 2: the user's answers to Needs-you claims ----


def _shift_block_claims(claims: Sequence[Claim], edited: Claim, inserted: int) -> None:
    removed = edited.span_end - edited.span_start
    for other in claims:
        if other.id == edited.id or other.block_index != edited.block_index:
            continue
        if other.bucket in ("blocked", "removed"):
            continue
        if other.span_start >= edited.span_end:
            other.span_start += inserted - removed
            other.span_end += inserted - removed


async def confirm(
    db: AsyncSession,
    claim: Claim,
    claims: Sequence[Claim],
    *,
    url: str | None,
    note: str | None,
    now: datetime,
) -> Evidence:
    """The user vouches for the claim with a link or a note. It joins the ledger as their own
    evidence, and every open claim in this application with the same value is Verified too."""
    if not url and not (note and note.strip()):
        raise DecisionError("Add a link or a short note that backs this up.")
    existing = (
        await db.execute(
            select(Evidence).where(
                Evidence.user_id == claim.user_id,
                Evidence.type == claim.type,
                Evidence.normalized_value == claim.normalized_value,
                Evidence.source == "user_confirmed",
            )
        )
    ).scalar_one_or_none()
    evidence = existing or Evidence(
        user_id=claim.user_id,
        claim_text=claim.text,
        type=claim.type,
        normalized_value=claim.normalized_value,
        source="user_confirmed",
        source_ref=(url or f"Note: {note.strip() if note else ''}")[:MAX_NOTE],
        confirmed_by="user",
        confidence=1.0,
        created_at=now,
    )
    if existing is None:
        db.add(evidence)
        await db.flush()
    for other in claims:
        if (
            other.bucket == "needs_you"
            and other.type == claim.type
            and other.normalized_value == claim.normalized_value
        ):
            other.bucket = "verified"
            other.evidence_id = evidence.id
            other.user_decision = "confirmed"
            other.decided_at = now
    audit.record(
        db,
        user_id=claim.user_id,
        actor="user",
        action="claim_confirmed",
        entity="claim",
        entity_id=claim.id,
        evidence_id=evidence.id,
        with_link=bool(url),
    )
    return evidence


def remove(
    blocks: list[Block], claim: Claim, claims: Sequence[Claim], *, now: datetime, decision: str
) -> None:
    """Cuts the claim's words from its bullet. A bullet left too short is dropped with its claims."""
    block = blocks[claim.block_index]
    text = replace_span(block.text, claim.span_start, claim.span_end, "")
    if too_short(text):
        blocks[claim.block_index] = replace(block, text="")
        for other in claims:
            if other.block_index == claim.block_index and other.bucket not in (
                "blocked",
                "removed",
            ):
                other.bucket, other.user_decision, other.decided_at = "removed", "removed", now
    else:
        blocks[claim.block_index] = replace(block, text=text)
        _relocate(blocks[claim.block_index].text, claim, claims)
    claim.bucket, claim.user_decision, claim.decided_at = "removed", decision, now


def _relocate(text: str, changed: Claim, claims: Sequence[Claim]) -> None:
    """After an edit, find each remaining claim of the bullet again by its words."""
    cursor = 0
    for other in sorted(
        (c for c in claims if c.block_index == changed.block_index and c.id != changed.id),
        key=lambda c: c.span_start,
    ):
        if other.bucket in ("blocked", "removed"):
            continue
        index = text.find(other.text, cursor)
        if index < 0:
            index = text.find(other.text)
        if index < 0:
            other.bucket, other.user_decision = "removed", "removed"
            continue
        other.span_start, other.span_end = index, index + len(other.text)
        cursor = other.span_end


async def mark_false(
    db: AsyncSession, blocks: list[Block], claim: Claim, claims: Sequence[Claim], *, now: datetime
) -> None:
    """Removes the claim and blocks its value for this user, so it is never suggested again."""
    remove(blocks, claim, claims, now=now, decision="false")
    known = (
        await db.execute(
            select(ClaimBlocklist.id).where(
                ClaimBlocklist.user_id == claim.user_id,
                ClaimBlocklist.normalized_value == claim.normalized_value,
            )
        )
    ).first()
    if known is None:
        db.add(
            ClaimBlocklist(
                user_id=claim.user_id,
                normalized_value=claim.normalized_value,
                reason="Marked false in an application",
                created_at=now,
            )
        )
    audit.record(
        db,
        user_id=claim.user_id,
        actor="user",
        action="claim_marked_false",
        entity="claim",
        entity_id=claim.id,
    )


async def edit(
    blocks: list[Block],
    claim: Claim,
    claims: Sequence[Claim],
    new_text: str,
    *,
    ledger: Ledger,
    taxonomy: Taxonomy,
    similarity: Similarity,
    threshold: float,
    now: datetime,
) -> None:
    """Rewords the claim in place and routes the new wording again."""
    new_text = " ".join(new_text.split())
    if not new_text:
        raise DecisionError("Write the new wording, or remove the claim instead.")
    block = blocks[claim.block_index]
    text = block.text[: claim.span_start] + new_text + block.text[claim.span_end :]
    blocks[claim.block_index] = replace(block, text=text)
    claim.span_end = claim.span_start + len(new_text)
    claim.text = new_text[:500]
    _relocate(text, claim, claims)
    [sim] = await similarity.scores([(text, block.original)])
    found = FoundClaim(new_text, claim.type, claim.span_start, claim.span_end, claim.origin)  # type: ignore[arg-type]
    routing = route(
        found,
        ledger=ledger,
        taxonomy=taxonomy,
        source_line=block.original,
        similarity=sim,
        threshold=threshold,
    )
    claim.normalized_value = routing.normalized[:500]
    claim.similarity = round(sim, 4)
    claim.bucket = "needs_you" if routing.bucket == "blocked" else routing.bucket
    claim.user_decision, claim.decided_at = "edited", now
