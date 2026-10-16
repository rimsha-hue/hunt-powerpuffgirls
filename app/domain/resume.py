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
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?<!\w)(\+?\d[\d\s().-]{7,}\d)(?!\w)")
_LINKEDIN_RE = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[\w\-%]+/?", re.I)
_GITHUB_RE = re.compile(r"(?:https?://)?github\.com/([A-Za-z0-9-]{1,39})(?![\w/-]*/[\w-])", re.I)

MAX_LINE_LENGTH = 500


@dataclass(frozen=True)
class ParsedLine:
    line_no: int
    section: str
    text: str


@dataclass(frozen=True)
class ContactInfo:
    name: str | None
    email: str | None
    phone: str | None
    linkedin: str | None
    github_username: str | None


def _heading_section(line: str) -> str | None:
    key = re.sub(r"[^a-z& ]", "", line.lower()).strip()
    key = re.sub(r"\s+", " ", key)
    if len(key) > 40:
        return None
    return _HEADING_LOOKUP.get(key)


def clean_line(raw: str) -> str:
    line = _BULLET_RE.sub("", raw).strip()
    line = re.sub(r"\s+", " ", line)
    return line[:MAX_LINE_LENGTH]


def split_resume(text: str) -> list[ParsedLine]:
    """Numbered, cleaned, non-empty lines. Headings set the section and are kept as lines too."""
    lines: list[ParsedLine] = []
    section = "header"
    for raw in text.splitlines():
        line = clean_line(raw)
        if not line:
            continue
        heading = _heading_section(line)
        if heading:
            section = heading
        lines.append(ParsedLine(line_no=len(lines) + 1, section=section, text=line))
    return lines


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

    return ContactInfo(
        name=name,
        email=email.group(0) if email else None,
        phone=re.sub(r"\s+", " ", phone.group(1)).strip() if phone else None,
        linkedin=linkedin.group(0) if linkedin else None,
        github_username=github.group(1) if github else None,
    )
