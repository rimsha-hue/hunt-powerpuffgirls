"""Company facts (spec section 9): sentences about the company, verbatim from a fetched source.

A fact is only ever a sentence that appears in the source text, so "every company fact must be
supported by text from a fetched source" holds by construction. Sentences from the company's own
site come first; markdown, navigation and listing fragments are skipped.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.domain.locations import fold

MIN_FACT_WORDS = 8
MAX_FACT_WORDS = 45
MAX_FACTS = 8
CACHE_DAYS = 7

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“])")
_JUNK = re.compile(r"[#|\[\]{}<>*_=]|https?://|\bRead more\b|\bcookie|\bsubscribe\b", re.I)


@dataclass(frozen=True)
class CompanyFact:
    fact: str
    source_url: str


def company_key(name: str) -> str:
    return fold(name)[:200]


def _names(company: str) -> list[str]:
    key = fold(company)
    first = key.split(" ")[0] if key else ""
    return [n for n in {key, first} if len(n) >= 3]


def facts_from_text(text: str, company: str, url: str) -> list[CompanyFact]:
    names = _names(company)
    facts = []
    for raw in _SENTENCE.split(" ".join(text.split())):
        sentence = raw.strip()
        words = sentence.split()
        if not MIN_FACT_WORDS <= len(words) <= MAX_FACT_WORDS:
            continue
        if not sentence.endswith((".", "!")) or _JUNK.search(sentence):
            continue
        if sentence[0].islower() or re.search(r"\b(?:I|you|your)\b", sentence):
            continue  # first or second person: a quote or an ad, not a fact about the company
        if not any(f" {n} " in f" {fold(sentence)} " for n in names):
            continue
        facts.append(CompanyFact(sentence, url))
    return facts


def own_site(url: str, company: str) -> bool:
    host = urlsplit(url).netloc.lower()
    return any(n.replace(" ", "") in host.replace("-", "") for n in _names(company))


def select_facts(candidates: Iterable[CompanyFact], company: str) -> list[CompanyFact]:
    """Up to MAX_FACTS distinct facts, the company's own site first."""
    seen: set[str] = set()
    unique = []
    for fact in candidates:
        key = fold(fact.fact)
        if key not in seen:
            seen.add(key)
            unique.append(fact)
    unique.sort(key=lambda f: not own_site(f.source_url, company))
    return unique[:MAX_FACTS]
