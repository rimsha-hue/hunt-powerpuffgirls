"""Evidence ledger rules: what counts as a fact, how it is normalized, and when a line supports it.

The LLM may propose facts, but only code decides whether a fact enters the ledger: the cited line
must exist and must contain the fact's key tokens and every number in it.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, get_args

from app.domain.normalize import (
    content_tokens,
    degree_keys,
    find_degrees,
    find_quantities,
    normalize_org,
    normalize_text,
)
from app.domain.resume import ParsedLine
from app.domain.taxonomy import Taxonomy

FactType = Literal[
    "skill", "metric", "employer", "degree", "certification", "project", "role", "personal"
]
FACT_TYPES: tuple[str, ...] = get_args(FactType)
EvidenceSource = Literal["resume", "github", "certificate", "portfolio", "user_confirmed"]

MAX_CLAIM_LENGTH = 300
METRIC_SECTIONS = frozenset(
    {"experience", "projects", "awards", "summary", "activities", "publications"}
)
_CLAUSE_SPLIT = re.compile(r";|\s[|•]\s|,\s(?=[A-Za-z])|\.\s(?=[A-Z])")


@dataclass(frozen=True)
class Fact:
    claim_text: str
    type: FactType
    normalized_value: str
    line_no: int


def _number_keys(text: str) -> set[str]:
    return {q.key for q in find_quantities(text, include_years=True)}


def normalize_fact(fact_type: FactType, claim_text: str, taxonomy: Taxonomy) -> str | None:
    """Canonical comparison key for a fact, or None if the claim is unusable for its type."""
    claim_text = claim_text.strip()
    if not claim_text or len(claim_text) > MAX_CLAIM_LENGTH:
        return None
    match fact_type:
        case "skill":
            canonical = taxonomy.canonical(claim_text)
            if canonical is None:
                found = taxonomy.skills_in(claim_text)
                canonical = next(iter(found)) if len(found) == 1 else None
            return (canonical or normalize_text(claim_text)).lower() or None
        case "metric":
            keys = sorted(q.key for q in find_quantities(claim_text) if q.unit != "year-date")
            return "|".join(keys) or None
        case "degree":
            degrees = find_degrees(claim_text)
            if not degrees:
                return None
            # Prefer the most specific reading (one that names a field).
            return max(degrees, key=lambda d: bool(d.field)).key
        case "employer":
            return normalize_org(claim_text) or None
        case _:
            return normalize_text(claim_text) or None


def is_supported_by_line(
    fact_type: FactType, claim_text: str, line_text: str, taxonomy: Taxonomy
) -> bool:
    """True when `line_text` contains the claim's key tokens and every number it states."""
    if not _number_keys(claim_text) <= _number_keys(line_text):
        return False

    match fact_type:
        case "skill":
            canonical = taxonomy.canonical(claim_text)
            if canonical and canonical in taxonomy.skills_in(line_text):
                return True
            tokens = content_tokens(claim_text)
            return bool(tokens) and tokens <= content_tokens(line_text)
        case "metric":
            return bool(find_quantities(claim_text))
        case "degree":
            claimed = degree_keys(claim_text)
            if not claimed:
                return False
            on_line = degree_keys(line_text)
            levels_on_line = {k.split("|")[0] for k in on_line}
            # A claim may omit the field ("bachelor") but may never add one the line lacks.
            return all(k in on_line or ("|" not in k and k in levels_on_line) for k in claimed)
        case _:
            tokens = content_tokens(claim_text)
            return bool(tokens) and tokens <= content_tokens(line_text)


def _clause_around(text: str, start: int, end: int) -> str:
    bounds = [0, *(m.end() for m in _CLAUSE_SPLIT.finditer(text)), len(text) + 1]
    for left, right in zip(bounds, bounds[1:], strict=False):
        if left <= start < right:
            clause = text[left:right].strip(" ,;.|•")
            return clause if len(clause) <= 160 else text[max(0, start - 60) : end + 60].strip()
    return text[start:end]


def extract_facts_by_code(lines: Iterable[ParsedLine], taxonomy: Taxonomy) -> list[Fact]:
    """Deterministic pass: skills, degrees and metrics a regex can prove. Works without any LLM."""
    facts: list[Fact] = []
    for line in lines:
        if line.section == "header":
            continue
        for match in taxonomy.find(line.text):
            facts.append(Fact(match.canonical, "skill", match.canonical.lower(), line.line_no))
        for degree in find_degrees(line.text):
            facts.append(Fact(degree.surface.strip(" ,"), "degree", degree.key, line.line_no))
        for q in find_quantities(line.text):
            is_gpa = q.unit.startswith("gpa/")
            if q.unit in {"", "year-date"} or not (is_gpa or line.section in METRIC_SECTIONS):
                continue  # bare numbers are too ambiguous to stand alone as a metric
            clause = q.surface if is_gpa else _clause_around(line.text, q.start, q.end)
            facts.append(Fact(clause, "metric", q.key, line.line_no))
    return dedupe(facts)


def accept_proposed_facts(
    proposals: Iterable[tuple[FactType, str, int]],
    lines_by_no: dict[int, ParsedLine],
    taxonomy: Taxonomy,
) -> tuple[list[Fact], int]:
    """Keep proposed (type, claim, line_no) facts that the cited line supports. Returns (kept, rejected)."""
    kept: list[Fact] = []
    rejected = 0
    for fact_type, claim_text, line_no in proposals:
        line = lines_by_no.get(line_no)
        normalized = normalize_fact(fact_type, claim_text, taxonomy)
        if (
            line is None
            or normalized is None
            or not is_supported_by_line(fact_type, claim_text, line.text, taxonomy)
        ):
            rejected += 1
            continue
        kept.append(Fact(claim_text.strip(), fact_type, normalized, line_no))
    return kept, rejected


def dedupe(facts: Iterable[Fact]) -> list[Fact]:
    seen: set[tuple[str, str]] = set()
    unique: list[Fact] = []
    for fact in facts:
        key = (fact.type, fact.normalized_value)
        if key not in seen:
            seen.add(key)
            unique.append(fact)
    return unique
