"""Email and cover letter rules (spec sections 3C and 8), as pure functions.

Generated prose is split into sentence blocks so the Verifier can check each sentence against the
resume line it is closest to. The signature is the user's own contact text and is not a claim.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.domain.classify import ATTACHMENT_LABELS
from app.domain.cv import Block
from app.domain.style import style_problems

EMAIL_MIN_WORDS = 120
EMAIL_MAX_WORDS = 180
COVER_MIN_WORDS = 200
COVER_MAX_WORDS = 320
MAX_SUBJECT = 150

AttachmentStatus = Literal["attached", "link", "uploaded", "missing", "waived"]
READY: frozenset[str] = frozenset({"attached", "link", "uploaded", "waived"})

# "Use the subject line 'Data Analyst - Your Name'", "Subject: Application for ..."
_SUBJECT_RE = re.compile(
    r"subject(?:\s+line)?\s*(?:should\s+(?:be|read)|must\s+be|as|of|:|-)?\s*[\"“'‘]([^\"”'’\n]{5,140})[\"”'’]"
    r"|subject\s*:\s*([^\n]{5,140})",
    re.I,
)
# Placeholders in a requested subject. Bare words are replaced only when unambiguous ("Your
# Name", "Job Title"); a single word like "Role" only inside brackets, so "Job application" stays.
_PLACEHOLDERS = {
    r"[\[{<(]\s*(?:your|full|candidate'?s?)?\s*name\s*[\]}>)]|\b(?:your|full)\s+name\b": "name",
    r"[\[{<(]\s*(?:position|role|job)(?:\s+(?:title|name))?\s*[\]}>)]"
    r"|\b(?:job|position|role)\s+title\b": "title",
}
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])")


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][\w'%.+-]*", text))


def requested_subject(description: str) -> str | None:
    """The subject line format the posting asks for, if it states one."""
    for match in _SUBJECT_RE.finditer(description):
        found = (match.group(1) or match.group(2) or "").strip(" .\"'")
        if found and not found.lower().startswith(("line", "to ")):
            return found[:MAX_SUBJECT]
    return None


def subject_for(*, title: str, name: str, description: str) -> str:
    """The posting's own format with its placeholders filled, else a plain default."""
    template = requested_subject(description)
    if template is None:
        return f"Application for {title}, {name}".strip(", ")[:MAX_SUBJECT]
    values = {"name": name, "title": title}
    subject = template
    for pattern, key in _PLACEHOLDERS.items():
        subject = re.sub(pattern, values[key], subject, flags=re.I)
    return subject[:MAX_SUBJECT]


def prose_problems(text: str, min_words: int, max_words: int) -> list[str]:
    problems = style_problems(text)
    words = word_count(text)
    if not min_words <= words <= max_words:
        problems.append(f"is {words} words (keep it to {min_words}-{max_words})")
    return problems


def sentences(text: str) -> list[tuple[int, str]]:
    """(paragraph index, sentence) for prose; a greeting line is its own paragraph."""
    out: list[tuple[int, str]] = []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    for index, paragraph in enumerate(paragraphs):
        for sentence in _SENTENCE_END.split(paragraph):
            sentence = " ".join(sentence.split())
            if sentence:
                out.append((index, sentence))
    return out


def prose_blocks(
    text: str, section: str, sources: Sequence[tuple[int, int, str] | None]
) -> list[Block]:
    """Sentence blocks. `sources[i]` is (line id, line no, line text) of the resume line closest to
    sentence i, or None; the Verifier checks each sentence against that line."""
    blocks = []
    for (paragraph, sentence), source in zip(sentences(text), sources, strict=True):
        line_id, line_no, original = source if source else (0, 0, "")
        blocks.append(Block("bullet", section, sentence, line_id, line_no, paragraph, original))
    return blocks


def prose_text(blocks: Sequence[Block]) -> str:
    """Blocks back to text: sentences joined, paragraphs separated by a blank line."""
    paragraphs: dict[int, list[str]] = {}
    for block in blocks:
        if block.text:
            paragraphs.setdefault(block.entry, []).append(block.text)
    return "\n\n".join(" ".join(parts) for _, parts in sorted(paragraphs.items()))


def signature(name: str, email: str, links: Sequence[str]) -> str:
    return "\n".join(part for part in (name, email, *links) if part)


@dataclass(frozen=True)
class AttachmentItem:
    kind: str
    label: str
    status: AttachmentStatus
    detail: str | None = None  # a link, or a file name
    file_id: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
            "file_id": self.file_id,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "AttachmentItem":
        status = data["status"]
        if status not in ("attached", "link", "uploaded", "missing", "waived"):
            raise ValueError(f"unknown attachment status {status!r}")
        return cls(
            str(data["kind"]), str(data["label"]), status, data.get("detail"), data.get("file_id")
        )


def plan_attachments(required: Sequence[str], *, portfolio_url: str | None) -> list[AttachmentItem]:
    """What the email carries for each item the posting asks for. The CV and cover letter are
    generated; a portfolio is a link if the user has one; anything else needs the user."""
    items: list[AttachmentItem] = []
    for kind in dict.fromkeys(["cv", *required]):
        label = ATTACHMENT_LABELS.get(kind, kind.replace("_", " ").title())  # type: ignore[call-overload]
        if kind == "cv":
            items.append(AttachmentItem(kind, label, "attached", "Tailored CV (PDF)"))
        elif kind == "cover_letter":
            items.append(AttachmentItem(kind, label, "attached", "Cover letter (PDF)"))
        elif kind == "portfolio" and portfolio_url:
            items.append(AttachmentItem(kind, label, "link", portfolio_url))
        else:
            items.append(AttachmentItem(kind, label, "missing"))
    return items


def attachments_ready(items: Sequence[AttachmentItem]) -> bool:
    return all(item.status in READY for item in items)
