"""Job postings: one normalised shape for every source, dedupe keys, and deterministic filters.

The Scout asks each source for a role and place, but free sources ignore or loosely apply the
query, so code decides whether a posting fits: its title must match a target role and its place
must match a target location.
"""

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from app.domain.locations import Place, fold
from app.domain.normalize import normalize_org, normalize_text

MAX_TITLE = 300
MAX_COMPANY = 200
MAX_LOCATION = 200
MAX_URL = 1000
MAX_DESCRIPTION = 20_000

# A title must reach this relevance to some target role to be kept.
RELEVANCE_THRESHOLD = 0.5
# How much the job's head noun (engineer, analyst...) counts next to its specific words.
_HEAD_SHARE = 0.2

# Head nouns: "Machine Learning Engineer" and "Machine Learning Researcher" share the field but
# not the job. Variants map to one head so "Developer" and "Engineer" count as the same job.
JOB_HEAD_NOUNS = {
    "engineer": "engineer",
    "engineering": "engineer",
    "developer": "engineer",
    "programmer": "engineer",
    "architect": "architect",
    "analyst": "analyst",
    "scientist": "scientist",
    "researcher": "researcher",
    "designer": "designer",
    "manager": "manager",
    "consultant": "consultant",
    "specialist": "specialist",
    "administrator": "administrator",
    "intern": "intern",
    "internship": "intern",
    "trainee": "intern",
    "associate": "associate",
    "lead": "lead",
}
# Words that say nothing about which job it is.
_NOISE = frozenset(
    [
        "senior",
        "sr",
        "junior",
        "jr",
        "staff",
        "principal",
        "mid",
        "level",
        "entry",
        "graduate",
        "new",
        "grad",
        "i",
        "ii",
        "iii",
        "iv",
        "remote",
        "hybrid",
        "onsite",
        "on",
        "site",
        "contract",
        "contractor",
        "full",
        "part",
        "time",
        "fulltime",
        "parttime",
        "permanent",
        "temporary",
        "and",
        "or",
        "of",
        "the",
        "for",
        "with",
        "at",
        "in",
        "to",
        "a",
        "an",
        "all",
        "genders",
        "m",
        "f",
        "w",
        "d",
        "x",
    ]
)
# Common abbreviations in job titles, expanded so "ML Engineer" matches "Machine Learning Engineer".
_EXPANSIONS = {
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "genai": "generative artificial intelligence",
    "llm": "large language model",
    "nlp": "natural language processing",
    "cv": "computer vision",
    "ui": "user interface",
    "ux": "user experience",
    "qa": "quality assurance",
    "sre": "site reliability",
    "bi": "business intelligence",
    "swe": "software engineer",
    "fe": "frontend",
    "be": "backend",
}
_SPELLINGS = {"front": "frontend", "back": "backend", "fullstack": "full stack"}


@dataclass(frozen=True)
class JobPosting:
    """A posting normalised from any source. Text fields are plain text, never HTML."""

    source: str
    external_id: str
    title: str
    company: str
    location: str
    remote: bool
    url: str
    description: str
    posted_at: datetime | None
    apply_url: str | None = None
    country_code: str | None = None  # ISO 3166 alpha-2 when the source says
    expires_at: datetime | None = None  # when the source says applications close


def clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def clean_line(text: str) -> str:
    """Single-line field: collapse whitespace; U+FFFD (a dash lost upstream) becomes a dash."""
    text = text.replace("�", "-")
    return re.sub(r"\s+", " ", text).strip()


def dedupe_hash(company: str, title: str, location: str) -> str:
    key = "|".join((normalize_org(company), normalize_text(title), normalize_text(location)))
    return hashlib.sha256(key.encode()).hexdigest()


def _tokens(text: str) -> list[str]:
    words: list[str] = []
    for raw in re.findall(r"[a-z0-9+#]+", fold(text.replace("-", " ").replace("/", " "))):
        word = _SPELLINGS.get(raw, raw)
        words.extend(_EXPANSIONS.get(word, word).split())
    return words


def _split(text: str) -> tuple[set[str], set[str]]:
    """(specific words, head nouns) of a title."""
    specific: set[str] = set()
    heads: set[str] = set()
    for word in _tokens(text):
        if word in JOB_HEAD_NOUNS:
            heads.add(JOB_HEAD_NOUNS[word])
        elif word not in _NOISE and len(word) > 1:
            specific.add(word)
    return specific, heads


def title_relevance(role: str, title: str) -> float:
    """0..1: how well a posting's title matches a target role, from shared words and head noun."""
    role_specific, role_heads = _split(role)
    title_specific, title_heads = _split(title)
    head_ok = 1.0 if not role_heads or role_heads & title_heads else 0.0
    if not role_specific:
        return head_ok
    shared = len(role_specific & title_specific) / len(role_specific)
    return round((1 - _HEAD_SHARE) * shared + _HEAD_SHARE * head_ok, 4)


def best_role_relevance(roles: Iterable[str], title: str) -> float:
    return max((title_relevance(role, title) for role in roles), default=0.0)


def location_fit(posting: JobPosting, targets: Iterable[Place]) -> bool:
    """True when the posting is in one of the target places (or remote and remote is a target).
    With no targets every place fits."""
    targets = list(targets)
    if not targets:
        return True
    where = f" {fold(posting.location)} "
    for place in targets:
        if place.kind == "remote":
            if posting.remote:
                return True
        elif place.kind == "country":
            if posting.country_code:
                if posting.country_code.upper() == place.country_code:
                    return True
            elif f" {fold(place.name)} " in where:
                return True
        elif f" {fold(place.name)} " in where and (
            posting.country_code is None or posting.country_code.upper() == place.country_code
        ):
            return True
    return False


# ---- Is the posting still open ----------------------------------------------------------------
#
# Aggregators keep listing jobs after the employer closes them, and a repost resets the date they
# show. So a posting is checked at its source before it is offered: a "gone" status, or a page
# that says applications are closed.

GONE_STATUSES = frozenset({404, 410})
_CLOSED_RE = re.compile(
    r"\bno longer (?:accepting (?:applications|candidates)|available|open|active|taking)"
    r"|\b(?:position|job|role|posting|vacancy|opening) (?:has been|is now|is) "
    r"(?:filled|closed|expired|no longer available)"
    r"|\b(?:job|posting|listing|vacancy) (?:has )?expired"
    r"|\bapplications? (?:are |is |have )?(?:now )?closed"
    r"|\bnot (?:currently )?accepting applications"
    r"|\bthis job is closed",
    re.I,
)


def page_says_closed(text: str) -> bool:
    """True when a posting page states it no longer takes applications."""
    return bool(_CLOSED_RE.search(text))
