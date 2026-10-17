"""Verifier: finds every claim in the tailored CV and routes it (domain/claims.py), and applies the
user's answers to Needs-you claims (Gate 2).

Claims on the user's blocklist are removed from the text automatically. Confirming a claim adds it
to the ledger as user-confirmed evidence, so the same claim is Verified on the next application.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import cast

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Claim, ClaimBlocklist, Evidence
from app.domain.claims import (
    FoundClaim,
    Ledger,
    LedgerRow,
    Routing,
    clause_around,
    code_claims,
    from_context,
    is_intent,
    locate,
    merge,
    outside,
    pair_up,
    route,
)
from app.domain.cv import Block, replace_span, too_short
from app.domain.ledger import FACT_TYPES, MAX_CONTEXT_LENGTH, FactType, normalize_fact
from app.domain.normalize import normalize_text
from app.domain.taxonomy import Taxonomy
from app.integrations.embeddings import Similarity
from app.integrations.llm import LLMError, StructuredLLM
from app.services import audit

logger = structlog.get_logger()

MAX_NOTE = 300
# At most this many Needs-you questions per document: the riskiest are asked, the rest are kept as
# rewordings of the user's own lines (Inferred). 0 asks about every unproven claim (the eval).
MAX_QUESTIONS = 3
# Which unproven claims matter most: a wrong number, credential or employer misleads a recruiter
# more than a loosely worded skill phrase.
QUESTION_PRIORITY = (
    "metric",
    "degree",
    "school",
    "employer",
    "role",
    "certification",
    "publication",
    "award",
    "project",
    "skill",
)
# (type, normalised value, text) of the role or degree an organisation claim is stated with.
Pair = tuple[str, str, str]

CLAIM_SYSTEM = (
    "You list the claims a CV makes about the candidate. Bullets are given as [B4] text. A claim "
    "is one checkable statement: a skill or tool used, a result, a role or title held, an "
    "employer, a degree, a school attended, a certification, a project, or an action taken. "
    "Quote each claim exactly as it appears in its bullet, as a short span (a few words), and "
    "give its type. Do not paraphrase, merge bullets or add anything. What the candidate hopes "
    "or offers to do next, and thanks, are not claims."
)
CLAIM_TASK = (
    "List every claim in every bullet. Types: skill, metric, employer, degree, school, "
    "certification, project, role, personal, publication, award, other."
)


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
    pair: Pair | None = None


@dataclass
class Verification:
    blocks: list[Block]
    drafts: list[Draft]
    note: str | None


async def load_ledger(db: AsyncSession, user_id: int) -> Ledger:
    """One user's ledger: keys, entries, organisation names and blocklist."""
    rows = (
        await db.execute(
            select(
                Evidence.type, Evidence.normalized_value, Evidence.claim_text, Evidence.context
            ).where(Evidence.user_id == user_id)
        )
    ).all()
    blocked = (
        await db.execute(
            select(ClaimBlocklist.normalized_value).where(ClaimBlocklist.user_id == user_id)
        )
    ).scalars()
    return Ledger.build((LedgerRow(t, v, text, entry) for t, v, text, entry in rows), blocked)


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


def _find(
    block: Block,
    llm_found: Sequence[FoundClaim],
    *,
    ledger: Ledger,
    taxonomy: Taxonomy,
    ignore: Sequence[str],
    context: Sequence[str],
    subject: str = "",
) -> list[FoundClaim]:
    # An LLM claim inside a statement of intent ("I look forward to discussing...") asserts
    # nothing; any number, skill or organisation in it is still found by the code pass.
    stated = [c for c in llm_found if not is_intent(clause_around(block.text, c.start, c.end))]
    found = merge([*code_claims(block.text, taxonomy, ledger.names), *stated])
    return [
        c
        for c in outside(found, block.text, ignore)
        if not from_context(c, block.text, context, subject)
    ]


def _union(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Overlapping spans merged, in text order."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def pair_of(claim_type: FactType, text: str, taxonomy: Taxonomy) -> tuple[str, str]:
    return claim_type, normalize_fact(claim_type, text, taxonomy) or normalize_text(text)


def _route_block(
    block: Block,
    claims: Sequence[FoundClaim],
    sims: Mapping[tuple[int, int, int], float],
    index: int,
    *,
    ledger: Ledger,
    taxonomy: Taxonomy,
    threshold: float,
) -> list[tuple[FoundClaim, Routing, float, Pair | None]]:
    """Routes every claim of one bullet. An organisation stated with a role or degree is routed as
    part of that pair (claims.route), and the pair is kept so a confirmation can record it."""
    partners = pair_up([(c.type, c.start, c.end) for c in claims], block.text)
    out: list[tuple[FoundClaim, Routing, float, Pair | None]] = []
    for i, claim in enumerate(claims):
        partner = claims[partners[i]] if i in partners else None
        paired = pair_of(partner.type, partner.text, taxonomy) if partner else None
        sim = sims[(index, claim.start, claim.end)]
        routing = route(
            claim,
            ledger=ledger,
            taxonomy=taxonomy,
            source_line=block.original or None,
            similarity=sim,
            threshold=threshold,
            paired_with=paired,
        )
        pair = (*paired, partner.text) if partner and paired else None
        out.append((claim, routing, sim, pair))
    return out


async def _claim_similarities(
    blocks: Sequence[Block], found: Mapping[int, Sequence[FoundClaim]], similarity: Similarity
) -> dict[tuple[int, int, int], float]:
    """Per claim (block, start, end): the better of its sentence's and its clause's similarity to
    the block's source line. Unchanged lines are their own source (1.0); no source line, 0."""
    pairs: list[tuple[str, str]] = []
    keys: list[tuple[int, int, int] | int] = []
    for i, claims in found.items():
        block = blocks[i]
        if not block.rewritten or not block.original:
            continue
        pairs.append((block.text, block.original))
        keys.append(i)
        for claim in claims:
            clause = clause_around(block.text, claim.start, claim.end)
            if clause and clause != block.text:
                pairs.append((clause, block.original))
                keys.append((i, claim.start, claim.end))
    scores = dict(zip(keys, await similarity.scores(pairs), strict=True))
    out: dict[tuple[int, int, int], float] = {}
    for i, claims in found.items():
        block = blocks[i]
        for claim in claims:
            if not block.rewritten:
                sim = 1.0
            elif not block.original:
                sim = 0.0
            else:
                sim = max(scores.get(i, 0.0), scores.get((i, claim.start, claim.end), 0.0))
            out[(i, claim.start, claim.end)] = round(sim, 4)
    return out


async def verify(
    blocks: Sequence[Block],
    *,
    ledger: Ledger,
    taxonomy: Taxonomy,
    llm: StructuredLLM,
    similarity: Similarity,
    threshold: float,
    ignore: Sequence[str] = (),
    context: Sequence[str] = (),
    subject: str = "",
    max_questions: int = MAX_QUESTIONS,
) -> Verification:
    """Claims for every bullet. Blocked claims are cut from the text before anything is stored."""
    out = list(blocks)
    indexes = [i for i, b in enumerate(out) if b.kind == "bullet" and b.text]
    llm_found, note = await _llm_claims(out, indexes, llm)

    def find(block: Block, llm_claims: Sequence[FoundClaim]) -> list[FoundClaim]:
        return _find(
            block,
            llm_claims,
            ledger=ledger,
            taxonomy=taxonomy,
            ignore=ignore,
            context=context,
            subject=subject,
        )

    found = {i: find(out[i], [c for b, c in llm_found if b == i]) for i in indexes}
    sims = await _claim_similarities(out, found, similarity)

    drafts: list[Draft] = []
    for i in indexes:
        block = out[i]
        routed = _route_block(
            block, found[i], sims, i, ledger=ledger, taxonomy=taxonomy, threshold=threshold
        )
        blocked = [(c, r, s) for c, r, s, _ in routed if r.bucket == "blocked"]
        if blocked:
            text = block.text
            # Claims may overlap (an LLM claim around a code claim), so cut merged ranges.
            for start, end in reversed(_union((c.start, c.end) for c, _, _ in blocked)):
                text = replace_span(text, start, end, "")
            block = out[i] = replace(block, text="" if too_short(text) else text)
            drafts.extend(
                Draft(i, replace(c, start=0, end=0), "blocked", r.normalized, s, block.line_id)
                for c, r, s in blocked
            )
            # The wording changed, so the rest is found and scored again on the new text.
            again = {i: find(block, [])} if block.text else {}
            new_sims = await _claim_similarities(out, again, similarity)
            routed = [
                item
                for item in _route_block(
                    block,
                    again.get(i, []),
                    new_sims,
                    i,
                    ledger=ledger,
                    taxonomy=taxonomy,
                    threshold=threshold,
                )
                if item[1].bucket != "blocked"
            ]
        drafts.extend(
            Draft(i, c, r.bucket, r.normalized, s, block.line_id, pair) for c, r, s, pair in routed
        )
    return Verification(out, _cap_questions(drafts, max_questions), note)


def _cap_questions(drafts: list[Draft], limit: int) -> list[Draft]:
    """Keeps the `limit` riskiest Needs-you claims (code-found before model-found, then by type)
    and routes the rest as Inferred."""
    asked = [d for d in drafts if d.bucket == "needs_you"]
    if limit <= 0 or len(asked) <= limit:
        return drafts

    def risk(draft: Draft) -> tuple[int, int]:
        kind = draft.claim.type
        rank = (
            QUESTION_PRIORITY.index(kind) if kind in QUESTION_PRIORITY else len(QUESTION_PRIORITY)
        )
        return (0 if draft.claim.origin == "code" else 1, rank)

    keep = {id(d) for d in sorted(asked, key=risk)[:limit]}
    return [
        replace(d, bucket="inferred") if d.bucket == "needs_you" and id(d) not in keep else d
        for d in drafts
    ]


def claim_rows(
    application_id: int, user_id: int, drafts: Sequence[Draft], document: str = "cv"
) -> list[Claim]:
    return [
        Claim(
            application_id=application_id,
            user_id=user_id,
            document=document,
            block_index=d.block_index,
            text=d.claim.text[:500],
            type=d.claim.type,
            normalized_value=d.normalized[:500],
            span_start=d.claim.start,
            span_end=d.claim.end,
            bucket=d.bucket,
            origin=d.claim.origin,
            cited_line_id=d.line_id or None,
            similarity=d.similarity,
            pair_type=d.pair[0] if d.pair else None,
            pair_value=d.pair[1][:500] if d.pair else None,
            pair_text=d.pair[2][:500] if d.pair else None,
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
    evidence, and every open claim in this application with the same value is Verified too.

    An organisation stated with a role or degree ("ML Engineer at Acme") is confirmed as that
    pair: both join the ledger under one entry, so the pair is Verified next time."""
    # A link or note is optional: the user's word is the evidence, labelled as such.
    source_ref = (url or (f"Note: {note.strip()}" if note and note.strip() else "Your word"))[
        :MAX_NOTE
    ]
    entry = f"{claim.pair_text} · {claim.text}"[:MAX_CONTEXT_LENGTH] if claim.pair_type else None
    evidence = await _confirmed_evidence(
        db, claim.user_id, claim.type, claim.normalized_value, claim.text, entry, source_ref, now
    )
    if claim.pair_type and claim.pair_value and claim.pair_text:
        await _confirmed_evidence(
            db,
            claim.user_id,
            claim.pair_type,
            claim.pair_value,
            claim.pair_text,
            entry,
            source_ref,
            now,
        )
    for other in claims:
        if (
            other.bucket == "needs_you"
            and other.type == claim.type
            and other.normalized_value == claim.normalized_value
            and (other.pair_type, other.pair_value) == (claim.pair_type, claim.pair_value)
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


async def _confirmed_evidence(
    db: AsyncSession,
    user_id: int,
    fact_type: str,
    value: str,
    text: str,
    entry: str | None,
    source_ref: str,
    now: datetime,
) -> Evidence:
    existing = (
        await db.execute(
            select(Evidence).where(
                Evidence.user_id == user_id,
                Evidence.type == fact_type,
                Evidence.normalized_value == value,
                Evidence.source == "user_confirmed",
                Evidence.context.is_(None) if entry is None else Evidence.context == entry,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    evidence = Evidence(
        user_id=user_id,
        claim_text=text,
        type=fact_type,
        normalized_value=value,
        source="user_confirmed",
        source_ref=source_ref,
        context=entry,
        confirmed_by="user",
        confidence=1.0,
        created_at=now,
    )
    db.add(evidence)
    await db.flush()
    return evidence


def remove(
    blocks: list[Block], claim: Claim, claims: Sequence[Claim], *, now: datetime, decision: str
) -> None:
    """Cuts the claim's words from its bullet. A bullet left too short is dropped with its claims."""
    block = blocks[claim.block_index]
    old = (claim.span_start, claim.span_end)
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
        delta = len(text) - len(block.text)
        _relocate(blocks[claim.block_index].text, claim, claims, old=old, delta=delta)
    claim.bucket, claim.user_decision, claim.decided_at = "removed", decision, now


def _relocate(
    text: str, changed: Claim, claims: Sequence[Claim], *, old: tuple[int, int], delta: int
) -> None:
    """After the words at `old` were replaced (the text grew by `delta`), find each remaining claim
    of the bullet again. Claims inside the replaced words are gone. A claim that overlapped them
    ("Senior backend and AI engineer" when "AI" changed) keeps its place with its new words and
    goes back to Needs you, so changing part of a statement never lets the rest through unseen."""
    start, end = old
    cursor = 0
    for other in sorted(
        (c for c in claims if c.block_index == changed.block_index and c.id != changed.id),
        key=lambda c: c.span_start,
    ):
        if other.bucket in ("blocked", "removed"):
            continue
        if other.span_start < end and start < other.span_end:
            if start <= other.span_start and other.span_end <= end:
                other.bucket, other.user_decision = "removed", "removed"
                continue
            new_start = min(other.span_start, start)
            new_end = max(other.span_end, end) + delta
            words = text[new_start:new_end]
            if not words.strip():
                other.bucket, other.user_decision = "removed", "removed"
                continue
            new_start += len(words) - len(words.lstrip())
            new_end -= len(words) - len(words.rstrip())
            other.span_start, other.span_end = new_start, new_end
            other.text = text[new_start:new_end][:500]
            other.bucket, other.user_decision, other.decided_at = "needs_you", None, None
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
    old = (claim.span_start, claim.span_end)
    claim.span_end = claim.span_start + len(new_text)
    claim.text = new_text[:500]
    _relocate(text, claim, claims, old=old, delta=len(text) - len(block.text))
    [sim] = await similarity.scores([(text, block.original)])
    claim.similarity = round(sim, 4)
    claim.user_decision, claim.decided_at = "edited", now

    # The edited claim and every organisation paired with it are routed again: changing "ML
    # Engineer" in "ML Engineer at Acme" changes what "Acme" claims.
    live = [
        c
        for c in claims
        if c.block_index == claim.block_index and c.bucket not in ("blocked", "removed")
    ]
    partners = pair_up([(c.type, c.span_start, c.span_end) for c in live], text)
    for i, other in enumerate(live):
        partner = live[partners[i]] if i in partners else None
        if other is not claim and partner is not claim:
            continue
        paired = pair_of(_fact_type(partner.type), partner.text, taxonomy) if partner else None
        routing = route(
            FoundClaim(
                other.text,
                _fact_type(other.type),
                other.span_start,
                other.span_end,
                "code" if other.origin == "code" else "llm",
            ),
            ledger=ledger,
            taxonomy=taxonomy,
            source_line=block.original,
            similarity=sim,
            threshold=threshold,
            paired_with=paired,
        )
        other.normalized_value = routing.normalized[:500]
        other.pair_type, other.pair_value, other.pair_text = (
            (paired[0], paired[1][:500], partner.text[:500])
            if partner and paired
            else (None, None, None)
        )
        if other is claim:
            claim.bucket = "needs_you" if routing.bucket == "blocked" else routing.bucket
        elif routing.bucket not in ("verified", "inferred"):
            # Its pair changed, so an earlier answer about it no longer holds.
            other.bucket, other.user_decision, other.decided_at = "needs_you", None, None
        elif other.bucket == "needs_you":
            other.bucket = routing.bucket  # the edit made the pair one the ledger proves


def _fact_type(value: str) -> FactType:
    if value not in FACT_TYPES:
        raise ValueError(f"unknown fact type {value!r}")
    return cast(FactType, value)
