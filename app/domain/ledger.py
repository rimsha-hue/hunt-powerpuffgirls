"""Evidence ledger rules: what counts as a fact, how it is normalized, and when a line supports it.

The LLM may propose facts, but only code decides whether a fact enters the ledger: the cited line
must exist and must contain the fact's key tokens and every number in it.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Literal, get_args

from app.domain.normalize import (
    Quantity,
    content_tokens,
    degree_keys,
    find_degrees,
    find_quantities,
    find_schools,
    normalize_org,
    normalize_text,
)
from app.domain.resume import DATE_RANGE_RE, ParsedLine, heading_section
from app.domain.roles import HEAD_NOUNS
from app.domain.taxonomy import Taxonomy

FactType = Literal[
    "skill",
    "metric",
    "employer",
    "degree",
    "school",
    "certification",
    "project",
    "role",
    "personal",
    "publication",
    "award",
    # Anything verifiable that fits no type above. Checked word for word against its line, so
    # nothing real is forced into a wrong type (a course issuer read as an "employer").
    "other",
]
FACT_TYPES: tuple[str, ...] = get_args(FactType)
EvidenceSource = Literal["resume", "github", "certificate", "portfolio", "user_confirmed"]

MAX_CLAIM_LENGTH = 300
MAX_CONTEXT_LENGTH = 200
METRIC_SECTIONS = frozenset(
    {"experience", "projects", "awards", "summary", "activities", "publications"}
)
# Sections made of entries: a short unbulleted title line (role, company, project name) followed
# by the lines that describe it. A metric is reported with the entry it belongs to.
ENTRY_SECTIONS = frozenset({"experience", "projects", "awards", "activities", "publications"})
# Facts that are true only together with their entry: a role was held at one employer, a degree
# was earned at one school, a result was reached in one project. They keep their entry's label,
# and the same value in two entries is two facts. Skills and certifications stand alone.
ENTRY_BOUND_TYPES = frozenset({"metric", "role", "employer", "degree", "school"})
TITLE_MAX_WORDS = 14
_CLAUSE_SPLIT = re.compile(r";|\s[|•]\s|,\s(?=[A-Za-z$€£]|\d)|\.\s(?=[A-Z])")
_DATE_RE = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?(?:\s*\d{4})?\b"
    r"|\b(?:19|20)\d{2}\b|\bpresent\b|\bcurrent\b|\[link\]",
    re.I,
)
_NON_NOUN_UNITS = ("%", "x", "usd", "eur", "gbp", "pkr")


@dataclass(frozen=True)
class Fact:
    claim_text: str
    type: FactType
    normalized_value: str
    line_no: int
    context: str | None = (
        None  # the entry a metric belongs to, e.g. "MediScan: AI Medicine Scanner"
    )


def strip_dates(text: str) -> str:
    out = _DATE_RE.sub(" ", text)
    out = re.sub(r"(?:\s*[–—-]\s*)+(?=\s*(?:$|[|,]))", " ", out)  # dangling range dashes
    return re.sub(r"\s+", " ", out).strip(" ,|–—-")


def _label(titles: Iterable[str]) -> str | None:
    joined = " · ".join(t for t in (strip_dates(t) for t in titles) if t)
    return joined[:MAX_CONTEXT_LENGTH] or None


def is_entry_title(line: ParsedLine) -> bool:
    """A short unbulleted line that names an entry (role, company, project), not one describing it."""
    return (
        line.section in ENTRY_SECTIONS
        and not line.bulleted
        and len(line.text.split()) <= TITLE_MAX_WORDS
        # A line stating a result ("reached 88% accuracy") describes an entry; it isn't one.
        and not any(q.unit not in {"", "year-date"} for q in find_quantities(line.text))
    )


def _work_entries(lines: list[ParsedLine]) -> dict[int, str | None]:
    """Entries in experience, projects and similar sections: a run of short unbulleted title lines
    (role, company, project name), then the lines describing it. Two title lines in a row are one
    entry ("AI Intern" over "AIO June - August 2026"); a longer run is read two lines at a time,
    because a title with no bullets is an entry of its own."""
    contexts: dict[int, str | None] = {}
    titles: list[ParsedLine] = []  # the current entry's run of title lines
    in_run = False
    for line in lines:
        if not line.bulleted and line.text.endswith(":") and len(line.text.split()) <= 4:
            contexts[line.line_no] = None  # a label such as "Key Contributions:" is not an entry
            continue
        if is_entry_title(line):
            titles = [*titles, line] if in_run else [line]
            in_run = True
            label = _label(t.text for t in titles[-2:])
            contexts[line.line_no] = label
            if len(titles) == 2:
                contexts[titles[0].line_no] = label
            continue
        in_run = False
        contexts[line.line_no] = _label(t.text for t in titles[-2:]) if titles else None
    return contexts


def _education_entries(lines: list[ParsedLine]) -> dict[int, str | None]:
    """Entries in Education: one per school attended. A line naming a degree or a school starts a
    new entry when the current one already has one, whichever order a resume writes them in
    ("NUST" then "BS Data Science", or "BS Data Science, NUST"). A second date range does the same,
    for schools no pattern recognises. Other lines (GPA, coursework) belong to the entry above."""
    entries: list[list[ParsedLine]] = []
    kinds: set[str] = set()
    for line in lines:
        found = set()
        if not line.bulleted:
            found |= {"degree"} if find_degrees(line.text) else set()
            found |= {"school"} if find_schools(line.text) else set()
            found |= {"dates"} if DATE_RANGE_RE.search(line.text) else set()
        if not entries or found & kinds:
            entries.append([])
            kinds = set()
        entries[-1].append(line)
        kinds |= found
    contexts: dict[int, str | None] = {}
    for entry in entries:
        named = [ln.text for ln in entry if not ln.bulleted and _names_entry(ln.text)]
        label = _label(named[:2])
        contexts.update({ln.line_no: label for ln in entry})
    return contexts


def _names_entry(text: str) -> bool:
    """A line that says what the entry is (school, degree), not a detail such as a GPA."""
    return not any(q.unit.startswith("gpa/") for q in find_quantities(text)) and bool(
        strip_dates(text)
    )


def entry_contexts(lines: Iterable[ParsedLine]) -> dict[int, str | None]:
    """For each line, the label of the entry it belongs to (up to two title lines, dates removed).

    Facts sharing a label share an entry: the role and employer of one job, the degree and school
    of one education entry, the results of one project."""
    contexts: dict[int, str | None] = {}
    section: list[ParsedLine] = []

    def close() -> None:
        if not section:
            return
        if section[0].section == "education":
            contexts.update(_education_entries(section))
        elif section[0].section in ENTRY_SECTIONS:
            contexts.update(_work_entries(section))
        else:
            contexts.update({ln.line_no: None for ln in section})
        section.clear()

    for line in lines:
        if heading_section(line.text) or (section and line.section != section[0].section):
            close()
        if heading_section(line.text):
            contexts[line.line_no] = None
            continue
        section.append(line)
    close()
    return contexts


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
        case "employer" | "school":
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


def _clause_span(text: str, position: int) -> tuple[int, int]:
    bounds = [0, *(m.end() for m in _CLAUSE_SPLIT.finditer(text)), len(text)]
    for left, right in zip(bounds, bounds[1:], strict=False):
        if left <= position < right:
            return left, right
    return 0, len(text)


def _clause_text(text: str, span: tuple[int, int], anchor: Quantity) -> str:
    clause = text[span[0] : span[1]].strip(" ,;.|•")
    if len(clause) <= 160:
        return clause
    return text[max(0, anchor.start - 60) : anchor.end + 60].strip()


_ASPIRATION_RE = re.compile(
    r"\b(?:plan(?:s|ning)?|aim(?:s|ing)?|hop(?:e|es|ing)|want(?:s|ing)?|wish|intend(?:s|ing)?|"
    r"aspir(?:e|es|ing)|dream|goal|someday|future|later|next year|consider(?:s|ing)?|"
    r"interested in|apply(?:ing)? (?:for|to)|would like)\b",
    re.I,
)


def degree_counts_as_evidence(line: ParsedLine) -> bool:
    """Degrees count only in an Education section, and never in a sentence about the future.

    Elsewhere "PhD" is usually a plan, a co-author or a job requirement, and treating it as held
    would verify a fabrication. Current enrolment ("Pursuing BS Data Science, 2023 - Present") counts.
    """
    return line.section == "education" and not _ASPIRATION_RE.search(line.text)


def school_counts_as_evidence(line: ParsedLine) -> bool:
    """A school is one the person attended only on an Education line, by the same rule as a
    degree. Elsewhere it is a course issuer, a client or a place the person spoke at."""
    return degree_counts_as_evidence(line)


TITLED_SECTIONS: dict[str, FactType] = {
    "certifications": "certification",
    "awards": "award",
    "publications": "publication",
}
_LINK_MARK = re.compile(r"\[(?:link|pdf|url|doi)\]", re.I)

# Employers and roles are evidence only where work is described. A certificate line names its
# issuer ("Crash Course in Data Science, Johns Hopkins University"); reading that as an employer
# would let the Verifier "prove" a job the person never had.
WORK_SECTIONS = frozenset({"experience", "activities", "summary"})
# Words that make a line about learning, not working. Named by what the line describes, not by
# provider, so a new course platform needs no change here.
_LEARNING_RE = re.compile(
    r"\b(?:certif\w*|courses?|coursework|speciali[sz]ation|bootcamp|workshop|training|mooc|"
    r"nanodegree|credential|badge|issued|completed|enrolled)\b",
    re.I,
)


def work_fact_counts_as_evidence(line: ParsedLine) -> bool:
    return line.section in WORK_SECTIONS and not _LEARNING_RE.search(line.text)


def is_version_number(text: str, q: Quantity) -> bool:
    """A number that is part of a name, not a result: "Gemini 2.0 Flash", "mBART-50", "GPT-4"."""
    if q.unit in _NON_NOUN_UNITS or q.unit.startswith("gpa/"):
        return False
    if q.start > 1 and text[q.start - 1] in "-_" and text[q.start - 2].isalnum():
        return True
    before = text[: q.start].rstrip()
    word = re.search(r"([A-Za-z][\w.+-]*)$", before)
    if not word or not word.group(1)[0].isupper():
        return False
    prior = before[: word.start()].rstrip()
    # A capitalised first word is a verb ("Led 5 engineers"), not a product name.
    return bool(prior) and prior[-1] not in ".;:•"


def _metric_facts(line: ParsedLine, context: str | None) -> list[Fact]:
    """One metric fact per clause, holding every quantity in that clause."""
    groups: dict[tuple[int, int], list[Quantity]] = {}
    facts: list[Fact] = []
    for q in find_quantities(line.text):
        if q.unit in {"", "year-date"}:
            continue  # bare numbers are too ambiguous to stand alone as a metric
        if q.unit.startswith("gpa/"):
            facts.append(Fact(q.surface, "metric", q.key, line.line_no, context))
            continue
        if line.section not in METRIC_SECTIONS or is_version_number(line.text, q):
            continue
        groups.setdefault(_clause_span(line.text, q.start), []).append(q)
    for span, quantities in groups.items():
        facts.append(
            Fact(
                _clause_text(line.text, span, quantities[0]),
                "metric",
                "|".join(sorted({q.key for q in quantities})),
                line.line_no,
                context,
            )
        )
    return facts


def extract_facts_by_code(lines: Iterable[ParsedLine], taxonomy: Taxonomy) -> list[Fact]:
    """Deterministic pass: skills, degrees, schools, titled facts and metrics. Works without any
    LLM. Degrees, schools and metrics carry the entry they belong to."""
    lines = list(lines)
    contexts = entry_contexts(lines)
    facts: list[Fact] = []
    for line in lines:
        if line.section == "header" or heading_section(line.text):
            continue
        entry = contexts.get(line.line_no)
        for match in taxonomy.find(line.text):
            facts.append(Fact(match.canonical, "skill", match.canonical.lower(), line.line_no))
        if degree_counts_as_evidence(line):
            for degree in find_degrees(line.text):
                facts.append(
                    Fact(degree.surface.strip(" ,"), "degree", degree.key, line.line_no, entry)
                )
        if school_counts_as_evidence(line):
            for school in find_schools(line.text):
                if school.key:
                    facts.append(Fact(school.surface, "school", school.key, line.line_no, entry))
        titled = TITLED_SECTIONS.get(line.section)
        # Certifications and awards are one per line; a publication is its title line, and the
        # bullets under it describe it.
        if titled and not (line.section == "publications" and line.bulleted):
            # A citation's year is part of the title; certificate and award dates are not.
            text = line.text if titled == "publication" else strip_dates(line.text)
            name = re.sub(r"\s+", " ", _LINK_MARK.sub("", text)).strip(" ,;|")
            if name and len(name) <= MAX_CLAIM_LENGTH:
                facts.append(Fact(name, titled, normalize_text(name), line.line_no))
        facts.extend(_metric_facts(line, entry))
    return dedupe(facts)


def accept_proposed_facts(
    proposals: Iterable[tuple[FactType, str, int]],
    lines_by_no: dict[int, ParsedLine],
    taxonomy: Taxonomy,
) -> tuple[list[Fact], int]:
    """Keep proposed (type, claim, line_no) facts that the cited line supports. Returns (kept, rejected)."""
    contexts = entry_contexts(sorted(lines_by_no.values(), key=lambda line: line.line_no))
    kept: list[Fact] = []
    rejected = 0
    for fact_type, claim_text, line_no in proposals:
        line = lines_by_no.get(line_no)
        normalized = normalize_fact(fact_type, claim_text, taxonomy)
        if (
            line is None
            or normalized is None
            or not is_supported_by_line(fact_type, claim_text, line.text, taxonomy)
            or (fact_type == "degree" and not degree_counts_as_evidence(line))
            or (fact_type == "school" and not school_counts_as_evidence(line))
            or (fact_type in ("employer", "role") and not work_fact_counts_as_evidence(line))
            # A metric needs a measured result; "k=4" or a bare count is not one.
            or (
                fact_type == "metric"
                and not any(q.unit not in ("", "year-date") for q in find_quantities(claim_text))
            )
        ):
            rejected += 1
            continue
        context = contexts.get(line_no) if fact_type in ENTRY_BOUND_TYPES else None
        kept.append(Fact(claim_text.strip(), fact_type, normalized, line_no, context))
    return kept, rejected


def dedupe(facts: Iterable[Fact]) -> list[Fact]:
    """One fact per (type, value, entry): "Software Engineer" at two companies is two facts."""
    seen: set[tuple[str, str, str | None]] = set()
    unique: list[Fact] = []
    for fact in facts:
        key = (fact.type, fact.normalized_value, fact.context)
        if key not in seen:
            seen.add(key)
            unique.append(fact)
    return unique


# ---- Same fact, different words -------------------------------------------------------------
#
# Sources word the same fact differently: "Director, ML & Deep Learning" on the resume and
# "Director of ML/DL" on the portfolio; "Machine Learning Specialization" and "Machine Learning
# Specialization, Andrew Ng". These are one fact with two sources, not two facts.
#
# Rule: compare content words after expanding skill names through the taxonomy (ML, DL, AI).
# Two facts are the same when their words nearly coincide, or when one only adds detail
# (an issuer, a person, a year). Words that change meaning (a skill, a job level, a role noun)
# are never "detail": "AI Intern" and "AI & Quantum Computing Intern" stay apart.

SAME_FACT_JACCARD = 0.8
_FILLER = frozenset({"course", "program", "programme", "certificate", "certification", "online"})
_LEVEL_WORDS = frozenset(
    [
        "senior",
        "junior",
        "principal",
        "staff",
        "chief",
        "mid",
        "entry",
        "lead",
        "head",
        "associate",
        "assistant",
        "trainee",
        "intern",
        "director",
        "manager",
        "vp",
        "i",
        "ii",
        "iii",
        "iv",
    ]
)
TEXT_FACT_TYPES = frozenset(
    {"employer", "school", "role", "certification", "project", "publication", "award", "other"}
)


def fact_words(text: str, taxonomy: Taxonomy) -> frozenset[str]:
    expanded = text
    for match in sorted(taxonomy.find(text), key=lambda m: -m.start):
        expanded = f"{expanded[: match.start]} {match.canonical} {expanded[match.end :]}"
    return frozenset(w for w in content_tokens(expanded) if w not in _FILLER)


def _meaning_words(taxonomy: Taxonomy) -> frozenset[str]:
    skill_words = {w for name in taxonomy.skills for w in content_tokens(name)}
    return frozenset(skill_words | HEAD_NOUNS | _LEVEL_WORDS)


def same_words(
    a: frozenset[str], b: frozenset[str], meaning_words: frozenset[str], *, min_shared: int = 2
) -> bool:
    if not a or not b:
        return False
    if a == b or len(a & b) / len(a | b) >= SAME_FACT_JACCARD:
        return True
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    return len(small) >= min_shared and small <= large and not (large - small) & meaning_words


def _metric_keys(value: str) -> frozenset[str]:
    return frozenset(value.split("|"))


def reconcile(
    facts: Iterable[Fact],
    existing: Iterable[tuple[str, str, str]],
    taxonomy: Taxonomy,
) -> list[Fact]:
    """Merge facts that state the same thing, within a batch and against the ledger.

    `existing` holds (type, normalized_value, claim_text) already in the ledger from other
    sources. A new fact matching one reuses its normalized value, so both sources show on one
    ledger row. Within the batch, a later duplicate in the same entry is dropped; for metrics the
    reading that holds more numbers wins ("88%" folds into "88% ... 25 labels").
    """
    meaning = _meaning_words(taxonomy)
    known = [(t, v, fact_words(c, taxonomy)) for t, v, c in existing]

    def same(
        kind: str, value_a: str, words_a: frozenset[str], value_b: str, words_b: frozenset[str]
    ) -> bool:
        if value_a == value_b:
            return True
        if kind == "metric":
            keys_a, keys_b = _metric_keys(value_a), _metric_keys(value_b)
            return keys_a <= keys_b or keys_b <= keys_a
        # Project names are proper names, so one shared word is enough ("NAZAR", "nazar-core").
        min_shared = 1 if kind == "project" else 2
        return kind in TEXT_FACT_TYPES and same_words(
            words_a, words_b, meaning, min_shared=min_shared
        )

    kept: list[tuple[Fact, frozenset[str]]] = []
    for fact in facts:
        words = fact_words(fact.claim_text, taxonomy)
        for t, v, w in known:
            if t == fact.type and same(t, fact.normalized_value, words, v, w):
                fact = replace(fact, normalized_value=v)
                break
        for index, (other, other_words) in enumerate(kept):
            # Within one document, a fact repeated in another entry is another fact.
            if (
                other.type == fact.type
                and other.context == fact.context
                and same(
                    fact.type, fact.normalized_value, words, other.normalized_value, other_words
                )
            ):
                richer = fact.type == "metric" and len(_metric_keys(fact.normalized_value)) > len(
                    _metric_keys(other.normalized_value)
                )
                if richer:
                    kept[index] = (fact, words)
                break
        else:
            kept.append((fact, words))
    return dedupe(fact for fact, _ in kept)
