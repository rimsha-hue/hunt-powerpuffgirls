"""The tailored CV as ordered blocks built from the user's resume lines.

Every block comes from exactly one resume line. Headings, entry titles and lines outside
experience, projects and summary are kept word for word. Content lines in those sections
("bullets") may be rewritten and reordered, but only within their own entry, so a result can never
move from one job to another. Nothing is added that has no line behind it.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

from app.domain.ats import CvDocument
from app.domain.ledger import ENTRY_SECTIONS, entry_contexts, is_entry_title
from app.domain.resume import ParsedLine, heading_section

BlockKind = Literal["heading", "title", "bullet", "text"]
TAILORABLE_SECTIONS = frozenset({"summary", "experience", "projects"})
MAX_BULLET_LENGTH = 400
# A bullet left with fewer words than this after a removal says nothing; it is dropped.
MIN_BULLET_WORDS = 3
# Models like typographic hyphens, spaces and "3\u00d7"; plain ones keep text matching (and ATS) exact.
_PLAIN = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u00a0": " ",
        "\u202f": " ",
        "\u00d7": "x",
    }
)


@dataclass(frozen=True)
class SourceLine:
    id: int  # resume_lines.id
    line_no: int
    section: str
    text: str
    bulleted: bool


@dataclass(frozen=True)
class Block:
    kind: BlockKind
    section: str
    text: str
    line_id: int
    line_no: int
    entry: int  # blocks of one entry share this; bullets move only within it
    original: str  # the resume line's own words

    @property
    def rewritten(self) -> bool:
        return self.text != self.original

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Block":
        kind = data["kind"]
        if kind not in ("heading", "title", "bullet", "text"):
            raise ValueError(f"unknown block kind {kind!r}")
        return cls(
            kind=kind,
            section=str(data["section"]),
            text=str(data["text"]),
            line_id=int(data["line_id"]),
            line_no=int(data["line_no"]),
            entry=int(data["entry"]),
            original=str(data["original"]),
        )


def base_blocks(lines: Sequence[SourceLine]) -> list[Block]:
    """The resume as blocks, in its own order, before any tailoring."""
    parsed = [ParsedLine(ln.line_no, ln.section, ln.text, ln.bulleted) for ln in lines]
    contexts = entry_contexts(parsed)
    parsed_by_no = {ln.line_no: ln for ln in parsed}
    blocks: list[Block] = []
    entry = 0
    for line in lines:
        if heading_section(line.text):
            entry += 1
            kind: BlockKind = "heading"
        elif line.section not in TAILORABLE_SECTIONS:
            kind = "text"
        elif (
            line.section == "summary"
            or line.bulleted
            or (contexts.get(line.line_no) and not is_entry_title(parsed_by_no[line.line_no]))
        ):
            kind = "bullet"  # a line describing an entry
        else:
            # An entry title (role, company, project name) opens a new entry.
            entry += 1 if line.section in ENTRY_SECTIONS else 0
            kind = "title"
        blocks.append(Block(kind, line.section, line.text, line.id, line.line_no, entry, line.text))
    return blocks


def plain(text: str) -> str:
    """Typographic hyphens, spaces and "×" as plain characters, so matching stays exact."""
    return text.translate(_PLAIN)


def apply_rewrites(
    blocks: Sequence[Block], rewrites: Sequence[tuple[int, str]]
) -> tuple[list[Block], int]:
    """Applies (line_id, new text) in the given order. Returns (blocks, rewrites rejected).

    A rewrite counts only for a bullet line, once per line, and within the length limit. Within
    each entry, rewritten bullets come first in the order given, then the untouched ones."""
    by_line = {b.line_id: b for b in blocks if b.kind == "bullet"}
    accepted: dict[int, str] = {}
    order: list[int] = []
    rejected = 0
    for line_id, text in rewrites:
        text = re.sub(r"\s+", " ", text.translate(_PLAIN)).strip()
        if line_id not in by_line or line_id in accepted or not text:
            rejected += 1
            continue
        if len(text) > MAX_BULLET_LENGTH:
            rejected += 1
            continue
        accepted[line_id] = text
        order.append(line_id)

    rank = {line_id: i for i, line_id in enumerate(order)}
    out: list[Block] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if block.kind != "bullet":
            out.append(block)
            i += 1
            continue
        run: list[Block] = []
        while i < len(blocks) and blocks[i].kind == "bullet" and blocks[i].entry == block.entry:
            run.append(blocks[i])
            i += 1
        run.sort(key=lambda b: (rank.get(b.line_id, len(rank)), b.line_no))
        out.extend(replace(b, text=accepted.get(b.line_id, b.text)) for b in run)
    return out, rejected


def reorder_by_relevance(blocks: Sequence[Block], score: Mapping[int, int]) -> list[Block]:
    """Without a rewrite: bullets within each entry, most relevant (score by line id) first."""
    ranked = sorted(
        (b for b in blocks if b.kind == "bullet"),
        key=lambda b: (-score.get(b.line_id, 0), b.line_no),
    )
    return apply_rewrites(blocks, [(b.line_id, b.text) for b in ranked])[0]


def replace_span(text: str, start: int, end: int, new: str) -> str:
    out = text[:start] + new + text[end:]
    out = re.sub(r"\s+([,.;:)])", r"\1", out)  # no space before punctuation
    out = re.sub(r"([(])\s+", r"\1", out)
    out = re.sub(r"(?:,\s*){2,}", ", ", out)  # ", ," left by a removal
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip(" ,;")


def too_short(text: str) -> bool:
    return len(re.findall(r"[A-Za-z0-9]+", text)) < MIN_BULLET_WORDS


def cv_document(blocks: Iterable[Block]) -> CvDocument:
    rows = list(blocks)
    return CvDocument(
        text="\n".join(b.text for b in rows if b.text),
        sections=frozenset(b.section for b in rows),
        has_tables_or_images=False,  # the PDF is rendered from these blocks as plain text
    )
