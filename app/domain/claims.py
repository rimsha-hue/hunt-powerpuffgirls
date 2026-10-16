"""Claim routing (spec section 5): every claim in generated text is Verified, Inferred or Needs you.

Claims come from two passes. The code pass finds every number, skill, degree and known employer,
so a claim can't be hidden by the LLM not listing it; the LLM pass adds the rest (roles, projects,
plain assertions). Code then routes each claim:

1. Verified: its normalised value matches ledger evidence of the same type (numbers by value and
   unit, skills through the taxonomy, degrees and employers by their canonical keys).
2. Inferred: no exact match, but its source line exists, the sentence is close to that line
   (embedding similarity at least the threshold), and the claim adds no number, skill, degree or
   employer the line doesn't state.
3. Needs you: everything else. Values on the user's blocklist are blocked outright.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.domain.ledger import FactType, is_version_number, normalize_fact
from app.domain.normalize import (
    content_tokens,
    degree_keys,
    find_degrees,
    find_quantities,
    normalize_org,
    normalize_text,
)
from app.domain.taxonomy import Taxonomy

Bucket = Literal["verified", "inferred", "needs_you", "blocked"]
Origin = Literal["code", "llm"]

# Organisation names written as "at Acme Corp" that the ledger may not know about.
# Each word has two or more characters, so "at Nimbus Labs I built" stops before "I".
_ORG_WORD = r"[A-Z][\w&'-]+(?:\.[\w&'-]+)*"
_AT_ORG_RE = re.compile(rf"\bat ({_ORG_WORD}(?: (?:{_ORG_WORD}|&|of))*)")
# Clause edges: a claim is also judged by its own clause, so framing words around it
# ("..., showing a focus on cost") don't dilute how close it is to its source line.
_CLAUSE_EDGE = re.compile(r"[,;:()]|\s[-\u2013\u2014]\s")


@dataclass(frozen=True)
class FoundClaim:
    text: str
    type: FactType
    start: int
    end: int
    origin: Origin
    cited_line_no: int | None = None


@dataclass(frozen=True)
class Ledger:
    """What the user has proven, as comparison keys per fact type."""

    values: Mapping[str, frozenset[str]]  # type -> normalised values
    metric_keys: frozenset[str]  # every single quantity key ("40%", "1200 user")
    blocklist: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def build(cls, rows: Iterable[tuple[str, str]], blocklist: Iterable[str] = ()) -> "Ledger":
        values: dict[str, set[str]] = {}
        metric_keys: set[str] = set()
        for fact_type, value in rows:
            values.setdefault(fact_type, set()).add(value)
            if fact_type == "metric":
                metric_keys.update(value.split("|"))
        return cls(
            {k: frozenset(v) for k, v in values.items()},
            frozenset(metric_keys),
            frozenset(blocklist),
        )

    def has(self, fact_type: str, value: str) -> bool:
        if fact_type == "metric":
            return all(key in self.metric_keys for key in value.split("|"))
        return value in self.values.get(fact_type, frozenset())


@dataclass(frozen=True)
class Routing:
    bucket: Bucket
    normalized: str


def code_claims(text: str, taxonomy: Taxonomy, known_employers: Iterable[str]) -> list[FoundClaim]:
    """Every number, skill, degree and employer in the text, by deterministic scanning."""
    found: list[FoundClaim] = []
    for q in find_quantities(text):
        # Dates and version numbers ("GPT-4", "Python 3") are names, not claims about results.
        if q.unit == "year-date" or is_version_number(text, q):
            continue
        found.append(FoundClaim(q.surface, "metric", q.start, q.end, "code"))
    for match in taxonomy.find(text):
        found.append(FoundClaim(match.surface, "skill", match.start, match.end, "code"))
    for degree in find_degrees(text):
        found.append(
            FoundClaim(degree.surface.strip(" ,"), "degree", degree.start, degree.end, "code")
        )
    folded = text.lower()
    for name in known_employers:
        key = name.strip()
        if len(key) < 3:
            continue
        for m in re.finditer(rf"(?<!\w){re.escape(key.lower())}(?!\w)", folded):
            found.append(
                FoundClaim(text[m.start() : m.end()], "employer", m.start(), m.end(), "code")
            )
    for m in _AT_ORG_RE.finditer(text):
        found.append(FoundClaim(m.group(1), "employer", m.start(1), m.end(1), "code"))
    return found


def outside(claims: Iterable[FoundClaim], text: str, phrases: Iterable[str]) -> list[FoundClaim]:
    """Claims not inside any occurrence of `phrases`: naming the job applied for ("the Senior
    Android Engineer role at Acme") says nothing about the candidate."""
    spans = []
    for phrase in phrases:
        if len(phrase.strip()) < 2:
            continue
        for m in re.finditer(re.escape(phrase.strip()), text, re.I):
            spans.append((m.start(), m.end()))
    return [c for c in claims if not any(s <= c.start and c.end <= e for s, e in spans)]


_FIRST_PERSON = re.compile(r"\bI\b|\b(?:my|me|we|our|us)\b", re.I)
_RELATIVE = re.compile(r"\b(?:that|which|who|whose|where|while|whereas)\b", re.I)


def _subclause(clause: str, phrase: str) -> str:
    """The part of a clause around a phrase, cut at relative words ("that", "which"...)."""
    at = clause.lower().find(phrase.lower())
    if at < 0:
        return clause
    left = max((m.end() for m in _RELATIVE.finditer(clause, 0, at)), default=0)
    right = min(
        (m.start() for m in _RELATIVE.finditer(clause, at + len(phrase))), default=len(clause)
    )
    return clause[left:right]


# Share of a clause's words that must come from a context text (a cited company fact, the
# question) for its claims to count as that source's, not the candidate's.
CONTEXT_OVERLAP = 0.6


def from_context(claim: FoundClaim, text: str, contexts: Iterable[str], subject: str = "") -> bool:
    """True when the claim belongs to a context text, not to the candidate:
    - its clause restates the text ("your $150 million commitment to the Genesis Mission") with
      every number in it;
    - it is a distinctive phrase copied from the text (a number, or two or more words that
      aren't a skill: "$150 million", "Genesis Mission"; "large language models" is a skill
      the candidate may well claim);
    - or its words appear in the text while its clause names the `subject` company
      ("Anthropic's work on Claude").
    "I built AI systems" and "I built apps with Claude" stay the candidate's claims."""
    clause = clause_around(text, claim.start, claim.end)
    tokens = content_tokens(clause)
    if not tokens:
        return False
    numbers = {q.key for q in find_quantities(claim.text, include_years=True)}
    distinctive = bool(numbers) or (len(claim.text.split()) >= 2 and claim.type != "skill")
    # A copied phrase is the company's only if nothing in its own sub-clause is first person:
    # "a company that commits $150 million" yes, "I served 300 factories" no.
    about_candidate = _FIRST_PERSON.search(_subclause(clause, claim.text)) is not None
    names_subject = bool(subject) and subject.lower() in clause.lower()
    for context in contexts:
        context_numbers = {q.key for q in find_quantities(context, include_years=True)}
        overlap = len(tokens & content_tokens(context)) / len(tokens)
        if numbers <= context_numbers and overlap >= CONTEXT_OVERLAP:
            return True
        copied = claim.text.lower() in context.lower()
        if copied and ((distinctive and not about_candidate) or names_subject):
            return True
    return False


def clause_around(text: str, start: int, end: int) -> str:
    left = max((m.end() for m in _CLAUSE_EDGE.finditer(text, 0, start)), default=0)
    right = min((m.start() for m in _CLAUSE_EDGE.finditer(text, end)), default=len(text))
    return text[left:right].strip()


def merge(claims: Iterable[FoundClaim]) -> list[FoundClaim]:
    """Non-overlapping claims in text order. Code claims win over LLM claims for the same words;
    otherwise the earlier, then the longer, claim is kept."""
    ordered = sorted(claims, key=lambda c: (c.origin != "code", c.start, -(c.end - c.start)))
    kept: list[FoundClaim] = []
    for claim in ordered:
        if claim.end <= claim.start:
            continue
        if any(claim.start < k.end and k.start < claim.end for k in kept):
            continue
        kept.append(claim)
    return sorted(kept, key=lambda c: c.start)


def locate(text: str, quote: str) -> tuple[int, int] | None:
    """Span of an LLM-quoted claim in the text; None if it isn't quoted exactly."""
    quote = quote.strip()
    if not quote:
        return None
    index = text.find(quote)
    if index < 0:
        index = text.lower().find(quote.lower())
    return (index, index + len(quote)) if index >= 0 else None


def _grounded(claim: FoundClaim, normalized: str, line: str, taxonomy: Taxonomy) -> bool:
    """The claim states nothing the source line doesn't: no new number, skill, degree, employer."""
    line_numbers = {q.key for q in find_quantities(line, include_years=True)}
    claim_numbers = {q.key for q in find_quantities(claim.text, include_years=True)}
    if not claim_numbers <= line_numbers:
        return False
    if not degree_keys(claim.text) <= degree_keys(line):
        return False
    line_skills = {s.lower() for s in taxonomy.skills_in(line)}
    if not {s.lower() for s in taxonomy.skills_in(claim.text)} <= line_skills:
        return False
    match claim.type:
        case "skill":
            # A taxonomy skill on the line, or words the line itself uses ("prompt compression").
            words = content_tokens(claim.text)
            return normalized in line_skills or (bool(words) and words <= content_tokens(line))
        case "employer":
            return set(normalize_org(claim.text).split()) <= set(normalize_text(line).split())
        case "metric":
            return bool(claim_numbers)
        case _:
            return True


def route(
    claim: FoundClaim,
    *,
    ledger: Ledger,
    taxonomy: Taxonomy,
    source_line: str | None,
    similarity: float | None,
    threshold: float,
) -> Routing:
    normalized = normalize_fact(claim.type, claim.text, taxonomy) or normalize_text(claim.text)
    if normalized in ledger.blocklist:
        return Routing("blocked", normalized)
    if ledger.has(claim.type, normalized):
        return Routing("verified", normalized)
    if (
        source_line is not None
        and similarity is not None
        and similarity >= threshold
        and _grounded(claim, normalized, source_line, taxonomy)
    ):
        return Routing("inferred", normalized)
    return Routing("needs_you", normalized)


def shift_spans(
    spans: Sequence[tuple[int, int]], at: int, removed: int, inserted: int
) -> list[tuple[int, int]]:
    """Spans after replacing `removed` characters at `at` with `inserted` characters."""
    delta = inserted - removed
    return [(s + delta, e + delta) if s >= at + removed else (s, e) for s, e in spans]
