"""Checks on a rendered CV, read back the way an applicant tracking system reads it.

A CV only counts as well formatted when its own text survives extraction: every line readable,
the name first, sections in order, and short enough to be read.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from app.domain.cv import Block, plain
from app.domain.normalize import content_tokens

CvTemplate = Literal["classic", "compact", "modern"]
CV_TEMPLATES: tuple[CvTemplate, ...] = get_args(CvTemplate)
# Recruiters and ATS guidance agree on one or two pages for early-career CVs.
MAX_CV_PAGES = 2
# How far into the extracted text the name may sit and still read as the CV's first line.
NAME_WINDOW = 200


@dataclass(frozen=True)
class FormatCheck:
    name: Literal["readable", "pages", "name_first", "section_order"]
    passed: bool
    detail: str


def _words(text: str) -> set[str]:
    return content_tokens(plain(text))


def format_checks(text: str, pages: int, blocks: Sequence[Block]) -> list[FormatCheck]:
    """`text` and `pages` come from reading the rendered PDF back."""
    found = _words(text)
    lines = [b for b in blocks if b.text.strip()]
    unreadable = [b for b in lines if not _words(b.text) <= found]
    header = next((b for b in lines if b.section == "header"), None)
    headings = [b.text for b in lines if b.kind == "heading"]
    folded = plain(text).lower()
    positions = [folded.find(h.lower()) for h in headings]
    in_order = all(p >= 0 for p in positions) and positions == sorted(positions)
    return [
        FormatCheck(
            "readable",
            not unreadable,
            "Every line reads back as text."
            if not unreadable
            else f"{len(unreadable)} line(s) don't read back cleanly.",
        ),
        FormatCheck(
            "pages",
            1 <= pages <= MAX_CV_PAGES,
            f"{pages} page(s); at most {MAX_CV_PAGES}.",
        ),
        FormatCheck(
            "name_first",
            header is None or _words(header.text) <= _words(text[:NAME_WINDOW]),
            "Your name is the first thing a reader sees.",
        ),
        FormatCheck(
            "section_order",
            in_order,
            "Sections read back in order." if in_order else "Sections read back out of order.",
        ),
    ]


def passes(checks: Sequence[FormatCheck]) -> bool:
    return all(c.passed for c in checks)
