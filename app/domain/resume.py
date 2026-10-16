"""Resume text to numbered lines with sections, plus contact details from the header."""

import re
from dataclasses import dataclass

SECTION_HEADINGS: dict[str, tuple[str, ...]] = {
    "summary": (
        "summary",
        "profile",
        "professional summary",
        "objective",
        "about me",
        "about",
        "career objective",
    ),
    "experience": (
        "experience",
        "work experience",
        "professional experience",
        "employment",
        "employment history",
        "work history",
        "internships",
        "internship experience",
        "relevant experience",
    ),
    "education": (
        "education",
        "academics",
        "academic background",
        "academic qualifications",
        "qualifications",
    ),
    "skills": (
        "skills",
        "technical skills",
        "core skills",
        "core competencies",
        "tech stack",
        "technologies",
        "tools and technologies",
        "tools & technologies",
        "key skills",
    ),
    "projects": (
        "projects",
        "personal projects",
        "academic projects",
        "selected projects",
        "key projects",
    ),
    "certifications": (
        "certifications",
        "certificates",
        "licenses and certifications",
        "licenses & certifications",
        "courses",
    ),
    "awards": (
        "awards",
        "achievements",
        "honors",
        "honours",
        "honors and awards",
        "awards and achievements",
    ),
    "publications": ("publications", "research"),
    "activities": (
        "volunteer",
        "volunteering",
        "leadership",
        "extracurricular activities",
        "activities",
    ),
    "languages": ("languages",),
    "interests": ("interests", "hobbies"),
}
_HEADING_LOOKUP = {h: section for section, names in SECTION_HEADINGS.items() for h in names}
_BULLET_RE = re.compile(r"^[\s•●▪◦○■□➢►▶✓✔\-–—*·]+")
_BULLET_START_RE = re.compile(r"^\s*(?:[•●▪◦○■□➢►▶✓✔*·]|[-–—]\s)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?<!\w)(\+?\d[\d\s().-]{7,}\d)(?!\w)")
_LINKEDIN_RE = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[\w\-%]+/?", re.I)
_GITHUB_RE = re.compile(r"(?:https?://)?github\.com/([A-Za-z0-9-]{1,39})(?![\w/-]*/[\w-])", re.I)
_URL_RE = re.compile(r"(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s|,]*)?", re.I)
# Hyphen-like characters PDFs use, normalised to "-" so "scikit‐learn" matches "scikit-learn".
_HYPHENS = str.maketrans({"‐": "-", "‑": "-", "­": "-"})
# Word endings that mark a line-end hyphen as hyphenation ("mem-/ory") rather than a compound.
_SUFFIXES = (
    "tion",
    "sion",
    "ment",
    "ing",
    "ity",
    "ence",
    "ance",
    "ory",
    "ary",
    "ics",
    "ogy",
    "ical",
    "ally",
    "ness",
    "able",
    "ible",
    "ive",
    "ous",
    "ful",
    "less",
    "ize",
    "ise",
    "ture",
    "sis",
    "ism",
    "ist",
    "ligence",
    "ure",
    "ent",
    "ant",
    "ial",
    "ian",
    "ly",
)
_TERMINAL = (".", ";", "!", "?")
WRAPPED_LINE_MIN_LENGTH = 70  # a physical line this long likely ran into the margin and wrapped

MAX_LINE_LENGTH = 800


@dataclass(frozen=True)
class ParsedLine:
    line_no: int
    section: str
    text: str
    bulleted: bool = False


@dataclass(frozen=True)
class ContactInfo:
    name: str | None
    email: str | None
    phone: str | None
    linkedin: str | None
    github_username: str | None
    portfolio_url: str | None = None


def heading_section(line: str) -> str | None:
    key = re.sub(r"[^a-z& ]", "", line.lower()).strip()
    key = re.sub(r"\s+", " ", key)
    if len(key) > 40:
        return None
    return _HEADING_LOOKUP.get(key)


def clean_line(raw: str) -> str:
    line = _BULLET_RE.sub("", raw.translate(_HYPHENS)).strip()
    line = re.sub(r"\s+", " ", line)
    return line[:MAX_LINE_LENGTH]


def _join_hyphenated(head: str, tail: str, document_words: set[str]) -> str:
    """Join "head-" and "tail" across a line break, deciding whether the hyphen is real."""
    stem = head[:-1]
    left = re.findall(r"[A-Za-z]+$", stem)
    right = re.findall(r"^[A-Za-z]+", tail)
    if left and right and tail[:1].islower():
        merged = (left[0] + right[0]).lower()
        hyphenated = f"{left[0]}-{right[0]}".lower()
        if merged in document_words and hyphenated not in document_words:
            return stem + tail
        if hyphenated not in document_words and right[0].lower().startswith(_SUFFIXES):
            return stem + tail
    return head + tail


@dataclass
class _Logical:
    section: str
    text: str
    bulleted: bool
    last_physical_length: int


def _continues(prev: _Logical, raw: str, text: str, section: str) -> bool:
    """Is this physical line a wrapped continuation of the previous logical line?"""
    if (
        section == "header"  # name, contact and links are separate even when lowercase
        or prev.section != section
        or heading_section(prev.text)
        or _BULLET_START_RE.match(raw)
    ):
        return False
    if prev.text.endswith("-") or text[:1].islower():
        return True
    if prev.text.endswith((",", "&", "(", "/", "+")):
        return True
    if section == "skills" and ":" not in text[:40]:
        return True  # "ML / AI: PyTorch, Keras," then "Hugging Face, NLP" is one skills line
    return (
        prev.bulleted
        and not prev.text.endswith(_TERMINAL)
        and prev.last_physical_length >= WRAPPED_LINE_MIN_LENGTH
    )


def split_resume(text: str) -> list[ParsedLine]:
    """Numbered logical lines: wrapped lines rejoined, bullets stripped, headings set sections.

    Headings are kept as lines too, so a fact's line number always points at what the user wrote.
    """
    raw_lines = [raw.translate(_HYPHENS) for raw in text.splitlines()]
    document_words = {
        w.lower() for w in re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)*", "\n".join(raw_lines))
    }

    logical: list[_Logical] = []
    section = "header"
    for raw in raw_lines:
        text_line = clean_line(raw)
        if not text_line:
            continue
        heading = heading_section(text_line)
        if heading:
            section = heading
            logical.append(_Logical(section, text_line, False, len(text_line)))
            continue
        if logical and _continues(logical[-1], raw, text_line, section):
            prev = logical[-1]
            if prev.text.endswith("-"):
                prev.text = _join_hyphenated(prev.text, text_line, document_words)
            else:
                prev.text = f"{prev.text} {text_line}"
            prev.text = prev.text[:MAX_LINE_LENGTH]
            prev.last_physical_length = len(text_line)
            continue
        logical.append(
            _Logical(section, text_line, bool(_BULLET_START_RE.match(raw)), len(text_line))
        )

    return [
        ParsedLine(line_no=i, section=item.section, text=item.text, bulleted=item.bulleted)
        for i, item in enumerate(logical, start=1)
    ]


def extract_contact(lines: list[ParsedLine]) -> ContactInfo:
    header = [line.text for line in lines if line.section == "header"][:8]
    blob = "\n".join(header) or "\n".join(line.text for line in lines[:8])

    email = _EMAIL_RE.search(blob)
    phone = _PHONE_RE.search(blob)
    linkedin = _LINKEDIN_RE.search(blob)
    github = _GITHUB_RE.search(blob)

    name = None
    for candidate in header[:3]:
        if (
            not _EMAIL_RE.search(candidate)
            and not re.search(r"\d|https?:|www\.|\|", candidate)
            and 1 < len(candidate.split()) <= 5
        ):
            name = candidate.title() if candidate.isupper() else candidate
            break

    portfolio = None
    for match in _URL_RE.finditer(blob):
        url = match.group(0).rstrip("/.")
        start = match.start()
        if blob[max(0, start - 1) : start] == "@" or re.search(
            r"linkedin\.com|github\.com", url, re.I
        ):
            continue
        if email and email.start() <= start < email.end():
            continue
        portfolio = url if url.lower().startswith("http") else f"https://{url}"
        break

    return ContactInfo(
        name=name,
        email=email.group(0) if email else None,
        phone=re.sub(r"\s+", " ", phone.group(1)).strip() if phone else None,
        linkedin=linkedin.group(0) if linkedin else None,
        github_username=github.group(1) if github else None,
        portfolio_url=portfolio,
    )
