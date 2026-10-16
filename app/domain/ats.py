"""ATS score (spec section 6): deterministic, and the same function scores the CV before and after.

1. Keywords per job, frozen when the job is first scored: taxonomy skills plus a few YAKE key
   phrases. Terms under "Requirements"/"Must have" weigh 2; everything else, including
   "Nice to have", weighs 1.
2. Coverage = weight of keywords found in the CV / total weight (synonym-aware, whole words).
3. Format checks: standard headings, contact info, no tables or images, at most two pages.
4. Score = round(100 * (0.85 * coverage + 0.15 * format)).
"""

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import yake

from app.domain.jobs import JOB_HEAD_NOUNS
from app.domain.locations import fold
from app.domain.normalize import STOPWORDS
from app.domain.taxonomy import Taxonomy

REQUIRED_WEIGHT = 2
OTHER_WEIGHT = 1
COVERAGE_SHARE = 0.85
FORMAT_SHARE = 0.15
# YAKE phrases are noisy, so only a few multi-word ones are kept, and only if repeated.
MAX_PHRASES = 5
MIN_PHRASE_OCCURRENCES = 2
YAKE_CANDIDATES = 30
# About two pages of a typical CV.
MAX_TWO_PAGE_WORDS = 1100

KeywordKind = Literal["skill", "phrase"]

_REQUIRED_HEADING = re.compile(
    r"^(?:requirements?|minimum requirements|must[- ]haves?|qualifications?|required (?:skills|"
    r"qualifications|experience)|what you(?:'ll)? (?:bring|need|have)|what we(?:'re)? looking for|"
    r"who you are|about you|you have|your profile|skills(?: required)?|key skills|"
    r"technical requirements|essential (?:skills|criteria))\b",
    re.I,
)
_NICE_HEADING = re.compile(
    r"^(?:nice[- ]to[- ]haves?|good[- ]to[- ]haves?|bonus(?: points)?|preferred(?: qualifications|"
    r" skills)?|pluses|a plus|desirable|it'?s a plus|extra credit)\b",
    re.I,
)
_HEADING_MAX_WORDS = 8
_GENERIC_WORDS = frozenset(
    [
        "experience",
        "experiences",
        "ability",
        "abilities",
        "skill",
        "skills",
        "strong",
        "excellent",
        "good",
        "great",
        "solid",
        "proven",
        "team",
        "teams",
        "work",
        "working",
        "years",
        "year",
        "knowledge",
        "understanding",
        "role",
        "job",
        "company",
        "candidate",
        "candidates",
        "opportunity",
        "opportunities",
        "including",
        "etc",
        "plus",
        "well",
        "able",
        "must",
        "will",
        "responsibilities",
        "requirements",
        "required",
        "preferred",
        "environment",
        "business",
        "new",
        "world",
        "help",
        "join",
        "us",
        "our",
        "we",
        "you",
        "your",
        "looking",
        "based",
        # Pay, seniority and work arrangement: not things a CV should contain.
        "salary",
        "range",
        "compensation",
        "senior",
        "junior",
        "remote",
        "fully",
        "onsite",
        "hybrid",
        "time",
    ]
)
_STANDARD_SECTIONS = frozenset({"education", "skills"})
_WORK_SECTIONS = frozenset({"experience", "projects"})
_EMAIL = re.compile(r"\b[\w.%+-]+@[\w-]+(?:\.[\w-]+)+\b")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


@dataclass(frozen=True)
class Keyword:
    term: str  # display form ("PostgreSQL", "predictive maintenance")
    key: str  # comparison key: lowercase canonical skill, or folded phrase
    kind: KeywordKind
    weight: int

    def to_json(self) -> dict[str, Any]:
        return {"term": self.term, "key": self.key, "kind": self.kind, "weight": self.weight}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Keyword":
        kind: KeywordKind = "phrase" if data["kind"] == "phrase" else "skill"
        return cls(str(data["term"]), str(data["key"]), kind, int(data["weight"]))


@dataclass(frozen=True)
class CvDocument:
    """What the scorer needs to know about a CV."""

    text: str
    sections: frozenset[str]
    # None when unknown (an uploaded file we no longer have); the check is then left out.
    has_tables_or_images: bool | None = None


@dataclass(frozen=True)
class AtsResult:
    score: int
    coverage: float
    format_score: float
    matched: list[Keyword]
    missing: list[Keyword]
    checks: dict[str, bool] = field(default_factory=dict)

    def detail_json(self) -> dict[str, Any]:
        return {
            "coverage": round(self.coverage, 4),
            "format": round(self.format_score, 4),
            "matched": [k.term for k in self.matched],
            "missing": [k.term for k in self.missing],
            "checks": self.checks,
        }


def _heading_weight(line: str) -> int | None:
    """The weight a heading line sets for the lines under it, or None if it isn't a heading."""
    text = line.strip().strip("#*:•-– ").strip()
    if not text or len(text.split()) > _HEADING_MAX_WORDS or text.endswith((".", ",")):
        return None
    if _NICE_HEADING.match(text):
        return OTHER_WEIGHT
    if _REQUIRED_HEADING.match(text):
        return REQUIRED_WEIGHT
    # All-caps headings ("RESPONSIBILITIES"), but not a skill on its own line ("SQL", "REST API").
    shouting = text.isupper() and all(len(w) >= 4 and w.isalpha() for w in text.split())
    if line.rstrip().endswith(":") or shouting or line.lstrip().startswith("#"):
        return OTHER_WEIGHT  # another heading (Responsibilities, Benefits...) ends the section
    return None


def weighted_sections(description: str) -> list[tuple[int, str]]:
    """(weight, line) for each line of a job description, from the headings above it."""
    current = OTHER_WEIGHT
    out: list[tuple[int, str]] = []
    for line in description.splitlines():
        heading = _heading_weight(line)
        if heading is None:
            # An inline "Nice to have Docker" bullet inside a requirements list is still optional.
            nice = _NICE_HEADING.match(line.strip().strip("•-–* "))
            out.append((OTHER_WEIGHT if nice else current, line))
            continue
        _label, _colon, rest = line.partition(":")
        if rest.strip():
            # "Nice to have: Docker" labels this line only; the section around it carries on.
            out.append((heading, rest))
        else:
            current = heading
    return out


def _phrase_ok(phrase: str, folded_description: str, taxonomy: Taxonomy) -> bool:
    words = phrase.split()
    # Single words from YAKE are mostly noise ("build", "model"); real skills come from the
    # taxonomy, so phrases must be two or three words.
    if not 2 <= len(words) <= 3:
        return False
    if any(not w.isalpha() or len(w) < 3 for w in words):
        return False
    if words[0] in _GENERIC_WORDS or words[-1] in _GENERIC_WORDS:
        return False
    # "and"/"of" joins two topics; a head noun makes it a job title ("backend engineers").
    if any(w in STOPWORDS or w.rstrip("s") in JOB_HEAD_NOUNS for w in words):
        return False
    if taxonomy.skills_in(phrase):
        return False  # already a skill keyword
    occurrences = len(re.findall(rf"(?<!\w){re.escape(phrase)}(?!\w)", folded_description))
    return occurrences >= MIN_PHRASE_OCCURRENCES


def extract_keywords(description: str, taxonomy: Taxonomy, company: str = "") -> list[Keyword]:
    """The job's keyword set: skills by section weight, then a few repeated key phrases."""
    sections = weighted_sections(description)
    skills: dict[str, Keyword] = {}
    for weight, line in sections:
        for name in taxonomy.skills_in(line):
            key = name.lower()
            if key not in skills or skills[key].weight < weight:
                skills[key] = Keyword(name, key, "skill", weight)

    folded = fold(description)
    company_words = set(fold(company).split())
    required_text = fold(" ".join(line for weight, line in sections if weight == REQUIRED_WEIGHT))
    phrases: list[Keyword] = []
    extractor = yake.KeywordExtractor(lan="en", n=3, top=YAKE_CANDIDATES, dedupLim=0.7)
    for raw, _score in extractor.extract_keywords(description):
        phrase = fold(raw)
        if (
            len(phrases) >= MAX_PHRASES
            or not _phrase_ok(phrase, folded, taxonomy)
            or set(phrase.split()) & company_words
            or any(p.key == phrase for p in phrases)
        ):
            continue
        in_required = re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", required_text)
        weight = REQUIRED_WEIGHT if in_required else OTHER_WEIGHT
        phrases.append(Keyword(phrase, phrase, "phrase", weight))

    ordered = sorted(skills.values(), key=lambda k: (-k.weight, k.term.lower()))
    return ordered + phrases


def keyword_found(keyword: Keyword, cv_skills: set[str], folded_cv: str) -> bool:
    if keyword.kind == "skill":
        return keyword.key in cv_skills
    return re.search(rf"(?<!\w){re.escape(keyword.key)}(?!\w)", folded_cv) is not None


def format_checks(cv: CvDocument) -> dict[str, bool]:
    checks = {
        "standard_headings": cv.sections >= _STANDARD_SECTIONS
        and bool(_WORK_SECTIONS & cv.sections),
        "contact_info": bool(_EMAIL.search(cv.text) or _PHONE.search(cv.text)),
        "two_pages_max": len(cv.text.split()) <= MAX_TWO_PAGE_WORDS,
    }
    if cv.has_tables_or_images is not None:
        checks["no_tables_or_images"] = not cv.has_tables_or_images
    return checks


def score_cv(
    keywords: Sequence[Keyword],
    cv: CvDocument,
    taxonomy: Taxonomy,
    supported: Collection[str] | None = None,
) -> AtsResult:
    """`supported`, for a generated CV: keyword keys the user's evidence backs. A keyword found in
    the CV counts only if it is supported, so tailoring can never raise the score with a keyword
    the ledger can't prove (spec section 6)."""
    cv_skills = {name.lower() for name in taxonomy.skills_in(cv.text)}
    folded_cv = fold(cv.text)
    matched = [
        k
        for k in keywords
        if keyword_found(k, cv_skills, folded_cv) and (supported is None or k.key in supported)
    ]
    missing = [k for k in keywords if k not in matched]
    total = sum(k.weight for k in keywords)
    # A posting with no recognisable keywords gives the CV nothing to cover.
    coverage = sum(k.weight for k in matched) / total if total else 0.0
    checks = format_checks(cv)
    format_score = sum(checks.values()) / len(checks)
    score = round(100 * (COVERAGE_SHARE * coverage + FORMAT_SHARE * format_score))
    return AtsResult(score, coverage, format_score, matched, missing, checks)


def cv_from_lines(lines: Iterable[tuple[str, str]]) -> CvDocument:
    """A CV from (section, text) resume lines. Tables and images are unknown for an upload."""
    rows = list(lines)
    return CvDocument(
        text="\n".join(text for _section, text in rows),
        sections=frozenset(section for section, _text in rows),
    )


def supported_keys(
    keywords: Sequence[Keyword],
    original: CvDocument,
    ledger_skills: Collection[str],
    taxonomy: Taxonomy,
) -> set[str]:
    """Keyword keys a tailored CV may use: skills the ledger proves, plus any keyword the user's
    own resume already contains."""
    already = {k.key for k in score_cv(keywords, original, taxonomy).matched}
    proven = {k.key for k in keywords if k.kind == "skill" and k.key in ledger_skills}
    return already | proven
