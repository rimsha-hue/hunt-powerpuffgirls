"""Deterministic normalizers for text, quantities (numbers with units), degrees, schools and
employers.

These decide whether two claims say the same thing, so they are strict on purpose:
"30%" equals "30 percent" but never "35%".
"""

import re
import unicodedata
from dataclasses import dataclass

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "the",
        "of",
        "for",
        "to",
        "in",
        "on",
        "at",
        "by",
        "with",
        "from",
        "as",
        "or",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "its",
        "this",
        "that",
        "my",
        "our",
        "their",
        "your",
        "i",
        "we",
        "they",
        "he",
        "she",
        "using",
        "used",
        "use",
        "via",
        "into",
        "over",
        "under",
        "across",
        "within",
        "per",
    ]
)

_MULTIPLIERS = {
    "k": 1e3,
    "thousand": 1e3,
    "m": 1e6,
    "mn": 1e6,
    "mm": 1e6,
    "million": 1e6,
    "b": 1e9,
    "bn": 1e9,
    "billion": 1e9,
}
_CURRENCIES = {
    "$": "usd",
    "usd": "usd",
    "€": "eur",
    "eur": "eur",
    "£": "gbp",
    "gbp": "gbp",
    "pkr": "pkr",
    "rs": "pkr",
    "rs.": "pkr",
}

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_PERCENT_RE = re.compile(rf"(?<![\w.])({_NUM})\s*(%|percent\b|per cent\b|pct\b)", re.I)
_CURRENCY_RE = re.compile(
    rf"(\$|€|£|\bUSD\b|\bEUR\b|\bGBP\b|\bPKR\b|\bRs\.?)\s*({_NUM})\s*(k|thousand|mn|mm|million|m|bn|billion|b)?(?![a-z])",
    re.I,
)
_GPA_RE = re.compile(r"(?<![\w.])(\d\.\d{1,2})\s*/\s*(4(?:\.0+)?|5(?:\.0+)?|10(?:\.0+)?)(?![\d.])")
_MULT_RE = re.compile(rf"(?<![\w.])({_NUM})\s*x(?![a-z0-9])", re.I)
_COUNT_RE = re.compile(
    rf"(?<![\w.$€£/])({_NUM})(\+)?\s*(k|thousand|mn|million|m|bn|billion|b)?(?![\w%.])\+?",
    re.I,
)
_YEAR_RANGE = range(1950, 2100)
_NOUN_STOP = STOPWORDS | {"times", "more", "less", "than", "faster", "slower"}
_UNIT_WORD = re.compile(r"\s+([A-Za-z][A-Za-z\-]{1,24})")
# A count's noun is looked for among this many words after it: "1,200 support agents".
MAX_UNIT_WORDS = 3
# A rate is part of the unit: "2M records daily" and "2M records a day" match each other, not
# "2M records per second".
_RATE_WORDS = {
    "hourly": "hour",
    "daily": "day",
    "nightly": "day",
    "weekly": "week",
    "monthly": "month",
    "quarterly": "quarter",
    "yearly": "year",
    "annually": "year",
    "annual": "year",
}
_PERIOD = r"second|sec|minute|min|hour|hr|day|night|week|month|quarter|year"
_RATE_PHRASE = re.compile(
    rf"\s+(?:per|a|an|each|every)\s+({_PERIOD})s?\b|\s*/\s*({_PERIOD})\b", re.I
)
_PERIOD_NAMES = {"sec": "second", "min": "minute", "hr": "hour", "night": "day"}
# "Tripled throughput" states 3x as surely as "3x throughput" does.
_WORD_MULT_RE = re.compile(r"\b(doubl|tripl|quadrupl)(?:e|ed|es|ing)\b", re.I)
_WORD_MULT = {"doubl": 2.0, "tripl": 3.0, "quadrupl": 4.0}


@dataclass(frozen=True)
class Quantity:
    value: float
    unit: str  # "%", "usd", "x", "gpa/4", or a singular noun such as "user", "" when bare
    surface: str
    start: int
    end: int

    @property
    def key(self) -> str:
        value = f"{self.value:.4f}".rstrip("0").rstrip(".")
        return f"{value}{self.unit}" if self.unit in {"%", "x"} else f"{value} {self.unit}".strip()


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^\w+#%./\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def content_tokens(text: str) -> set[str]:
    """Meaningful tokens: lowercased words and numbers, without stopwords or 1-letter noise."""
    tokens = re.findall(r"[a-z0-9][a-z0-9+#.]*", normalize_text(text))
    return {t.rstrip(".") for t in tokens if t not in STOPWORDS and (len(t) > 1 or t.isdigit())}


def _to_float(raw: str) -> float:
    return float(raw.replace(",", ""))


def _singular(word: str) -> str:
    word = word.lower()
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("sses", "uses", "xes", "ches", "shes")):  # classes, buses, boxes, batches
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _is_plural(word: str) -> bool:
    word = word.lower()
    return len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is"))


def _unit_noun(text: str, pos: int) -> tuple[str, int] | None:
    """(unit, end) for what a count measures. Modifiers between the number and the noun are
    skipped: "1,200 support agents" and "2M daily records" count agents and records, the same as
    "1,200 agents" and "2M records daily". The first plural among the next few words wins, else
    the first word; a stopword ends the phrase. A rate joins the unit: "record/day"."""
    words: list[re.Match[str]] = []
    for _ in range(MAX_UNIT_WORDS):
        m = _UNIT_WORD.match(text, pos)
        if m is None or m.group(1).lower() in _NOUN_STOP:
            break
        words.append(m)
        pos = m.end()
    nouns = [w for w in words if w.group(1).lower() not in _RATE_WORDS]
    if not nouns:
        return None
    pick = next((w for w in nouns if _is_plural(w.group(1))), nouns[0])
    unit, end = _singular(pick.group(1)), pick.end()
    rate = next((w for w in words if w.group(1).lower() in _RATE_WORDS), None)
    if rate is not None:
        end = max(end, rate.end())
        return f"{unit}/{_RATE_WORDS[rate.group(1).lower()]}", end
    phrase = _RATE_PHRASE.match(text, end)
    if phrase is not None:
        period = (phrase.group(1) or phrase.group(2)).lower()
        return f"{unit}/{_PERIOD_NAMES.get(period, period)}", phrase.end()
    return unit, end


def find_quantities(text: str, include_years: bool = False) -> list[Quantity]:
    """Every number in `text` with its unit. Calendar years are skipped unless asked for."""
    found: list[Quantity] = []
    taken: list[tuple[int, int]] = []

    def free(start: int, end: int) -> bool:
        return all(end <= s or start >= e for s, e in taken)

    def add(q: Quantity) -> None:
        if free(q.start, q.end):
            found.append(q)
            taken.append((q.start, q.end))

    for m in _GPA_RE.finditer(text):
        scale = int(float(m.group(2)))
        add(Quantity(float(m.group(1)), f"gpa/{scale}", m.group(0), m.start(), m.end()))
    for m in _PERCENT_RE.finditer(text):
        add(Quantity(_to_float(m.group(1)), "%", m.group(0), m.start(), m.end()))
    for m in _CURRENCY_RE.finditer(text):
        mult = _MULTIPLIERS.get((m.group(3) or "").lower(), 1.0)
        unit = _CURRENCIES.get(m.group(1).lower().strip(), "usd")
        add(Quantity(_to_float(m.group(2)) * mult, unit, m.group(0), m.start(), m.end()))
    for m in _MULT_RE.finditer(text):
        add(Quantity(_to_float(m.group(1)), "x", m.group(0), m.start(), m.end()))
    for m in _WORD_MULT_RE.finditer(text):
        add(Quantity(_WORD_MULT[m.group(1).lower()], "x", m.group(0), m.start(), m.end()))
    for m in _COUNT_RE.finditer(text):
        raw, suffix = m.group(1), m.group(3)
        value = _to_float(raw) * _MULTIPLIERS.get((suffix or "").lower(), 1.0)
        is_year = (
            not suffix
            and not m.group(2)
            and "." not in raw
            and "," not in raw
            and int(value) in _YEAR_RANGE
            and value == int(value)
        )
        if is_year and not include_years:
            continue
        unit = ""
        end = m.end(3) if suffix else m.end(1)
        if m.group(2):
            end = max(end, m.end(2))
        noun = _unit_noun(text, end if text[end : end + 1] != "+" else end + 1)
        if noun is not None:
            unit, end = noun
        if is_year:
            unit = "year-date"
        add(Quantity(value, unit, text[m.start() : end], m.start(), end))

    found.sort(key=lambda q: q.start)
    return found


def quantity_keys(text: str) -> set[str]:
    return {q.key for q in find_quantities(text)}


# ---- Degrees ---------------------------------------------------------------------------------

_FIELD_ABBREVIATIONS = {
    "cs": "computer science",
    "se": "software engineering",
    "it": "information technology",
    "ee": "electrical engineering",
    "ds": "data science",
    "ai": "artificial intelligence",
    "ce": "computer engineering",
    "me": "mechanical engineering",
}
KNOWN_FIELDS = (
    "computer science",
    "software engineering",
    "data science",
    "artificial intelligence",
    "information technology",
    "computer engineering",
    "electrical engineering",
    "mechanical engineering",
    "civil engineering",
    "chemical engineering",
    "mathematics",
    "applied mathematics",
    "statistics",
    "physics",
    "economics",
    "business administration",
    "finance",
    "accounting",
    "cyber security",
    "cybersecurity",
    "information systems",
    "telecommunication engineering",
    "electronics engineering",
    "biomedical engineering",
    "bioinformatics",
    "computational linguistics",
    "management sciences",
    "psychology",
    "machine learning",
    "robotics",
    "mechatronics",
    "industrial engineering",
    "architecture",
)

_LEVEL_PATTERNS: list[tuple[str, str]] = [
    ("phd", r"ph\.?\s?d\.?|doctorate|doctor of philosophy"),
    (
        "master",
        r"master'?s?(?:\s+of\s+(?:science|arts|engineering|business administration|philosophy))?|m\.?\s?phil|mba|m\.?\s?eng|m\.?\s?tech|m\.?sc",
    ),
    (
        "bachelor",
        r"bachelor'?s?(?:\s+of\s+(?:science|arts|engineering|business administration|technology))?|b\.?\s?eng|b\.?\s?tech|b\.?sc|bba",
    ),
    ("associate", r"associate(?:'s)?\s+degree|associate of (?:science|arts)"),
    ("intermediate", r"f\.?\s?sc|i\.?\s?cs|a[\s-]levels?|hssc|intermediate"),
]
# Short forms (BS, MS, BE...) are ambiguous ("MS Excel", "MA" the state), so they only count
# when followed by "in"/"of" or a recognised field of study.
_SHORT_FORMS = {
    "bs": "bachelor",
    "ba": "bachelor",
    "be": "bachelor",
    "ms": "master",
    "ma": "master",
}
_COMPOUND_RE = re.compile(r"\b(BS|MS)(CS|SE|IT|EE|DS|AI|CE)\b")
_FIELD_ALT = "|".join(re.escape(f) for f in sorted(KNOWN_FIELDS, key=len, reverse=True))
_FIELD_AFTER = re.compile(
    rf"^[\s.,:-]*(?:\((?:hons|honours)\)\s*)?(?:(?:in|of)\s+)?({_FIELD_ALT})\b|^[\s.,:-]*(?:in|of)\s+([A-Za-z][A-Za-z &]{{2,40}}?)(?=\s*(?:[,(|\n]|$|\s-\s|\s\d))",
    re.I,
)


@dataclass(frozen=True)
class Degree:
    level: str
    field: str  # "" when not stated
    surface: str
    start: int
    end: int

    @property
    def key(self) -> str:
        return f"{self.level}|{self.field}" if self.field else self.level


def _field_after(text: str, pos: int) -> tuple[str, int]:
    m = _FIELD_AFTER.match(text[pos:])
    if not m:
        return "", pos
    field = (m.group(1) or m.group(2) or "").strip().lower()
    field = re.sub(r"\s+", " ", field)
    return field, pos + m.end()


def find_degrees(text: str) -> list[Degree]:
    found: list[Degree] = []
    for m in _COMPOUND_RE.finditer(text):
        level = _SHORT_FORMS[m.group(1).lower()]
        found.append(
            Degree(level, _FIELD_ABBREVIATIONS[m.group(2).lower()], m.group(0), m.start(), m.end())
        )

    for level, pattern in _LEVEL_PATTERNS:
        for m in re.finditer(rf"(?<![\w.])(?:{pattern})(?![\w])", text, re.I):
            if any(d.start <= m.start() < d.end for d in found):
                continue
            field, end = _field_after(text, m.end())
            found.append(Degree(level, field, text[m.start() : end], m.start(), end))

    for m in re.finditer(r"(?<![\w.])(B\.?S|B\.?A|B\.?E|M\.?S|M\.?A)\.?(?![\w])", text):
        if any(d.start <= m.start() < d.end for d in found):
            continue
        field, end = _field_after(text, m.end())
        if not field:
            continue
        level = _SHORT_FORMS[m.group(1).replace(".", "").lower()]
        found.append(Degree(level, field, text[m.start() : end], m.start(), end))

    found.sort(key=lambda d: d.start)
    return found


def degree_keys(text: str) -> set[str]:
    return {d.key for d in find_degrees(text)}


# ---- Organisations ---------------------------------------------------------------------------

_LEGAL_SUFFIX_RE = re.compile(
    r"[\s,]*\b(?:\(?pvt\.?\)?\s*ltd\.?|private limited|inc\.?|llc|l\.l\.c\.|ltd\.?|limited|corp\.?|corporation|co\.|gmbh|plc|s\.a\.)$",
    re.I,
)


def normalize_org(name: str) -> str:
    name = unicodedata.normalize("NFKC", name).strip()
    previous = None
    while previous != name:
        previous = name
        name = _LEGAL_SUFFIX_RE.sub("", name).strip(" ,.-")
    return normalize_text(name)


# ---- Schools ---------------------------------------------------------------------------------
#
# A school is named by a capitalised phrase around an institution noun: "National University of
# Sciences and Technology", "Roots College", "School of Electrical Engineering and Computer
# Science". An acronym in brackets right after the name ("(NUST)") is the same school.

_INSTITUTION = (
    r"Universit(?:y|ies)|Colleges?|Institutes?|Schools?|Academy|Polytechnic|Conservatory|Seminary"
)
_NAME_WORD = r"[A-Z][\w&'.-]*"
# Small words a name can hold between capitalised words. "at" and "in" are left out, so
# "Pursuing BS at Lahore University" starts the name at "Lahore".
_NAME_JOIN = r"(?:of|the|for|and|&)"
_SCHOOL_RE = re.compile(
    rf"(?:{_NAME_WORD}\s+(?:{_NAME_JOIN}\s+)*)*"
    rf"(?:{_INSTITUTION})\b"
    rf"(?:\s+(?:{_NAME_JOIN}\s+)*{_NAME_WORD})*"
)
_INSTITUTION_WORD_RE = re.compile(rf"\b(?:{_INSTITUTION})\b")
_ACRONYM_RE = re.compile(r"\s*\(([A-Z][A-Za-z&-]{1,11})\)")


@dataclass(frozen=True)
class School:
    surface: str
    start: int
    end: int
    acronym: str | None = None  # "(NUST)" right after the name

    @property
    def key(self) -> str:
        return normalize_org(self.surface)

    @property
    def text(self) -> str:
        """The name as stored: "National University of Sciences and Technology (NUST)"."""
        return f"{self.surface} ({self.acronym})" if self.acronym else self.surface


_ALIAS_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<alias>[A-Z][A-Za-z&-]{1,11})\)\s*$")
_ACRONYM_SKIP = frozenset({"of", "the", "for", "and", "&", "in", "at"})


def org_names(text: str) -> tuple[str, ...]:
    """Every name an organisation goes by in its stored text: "Name (ACR)" is Name and ACR."""
    match = _ALIAS_RE.match(text.strip())
    if match:
        return match.group("name").strip(), match.group("alias")
    return (text.strip(),)


def is_acronym_of(short: str, name: str) -> bool:
    """True when `short` is the initials of `name`'s words: NUST for National University of
    Sciences and Technology, LUMS for Lahore University of Management Sciences."""
    short = short.strip()
    if not 2 <= len(short) <= 12 or " " in short or not short[:1].isupper():
        return False
    words = [w for w in re.findall(r"[A-Za-z]+", name) if w.lower() not in _ACRONYM_SKIP]
    return len(words) >= 2 and "".join(w[0] for w in words).lower() == short.lower()


def find_schools(text: str) -> list[School]:
    """Schools named in the text, with the acronym in brackets after a name as its alias."""
    found: list[School] = []
    for m in _SCHOOL_RE.finditer(text):
        name = m.group(0)
        noun = _INSTITUTION_WORD_RE.search(name)
        if noun is None:
            continue
        # "BS Data Science National University" without a comma: the name starts after the degree.
        cut = max((d.end for d in find_degrees(name) if d.end <= noun.start()), default=0)
        cut += len(name[cut:]) - len(name[cut:].lstrip(" ,"))
        start = m.start() + cut
        acronym = _ACRONYM_RE.match(text, m.end())
        found.append(
            School(text[start : m.end()], start, m.end(), acronym.group(1) if acronym else None)
        )
    return found
