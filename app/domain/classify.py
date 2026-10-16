"""Classifier: how to apply to a job (email, form or unknown) and which attachments it asks for.

Only sentences that talk about applying count, so a "references" in "references to our APIs" or a
support address in a footer doesn't become an application requirement.
"""

import re
from dataclasses import dataclass
from typing import Literal

ApplyMethod = Literal["email", "form", "unknown"]
Attachment = Literal["cv", "cover_letter", "portfolio", "transcript", "references", "work_samples"]

ATTACHMENT_LABELS: dict[Attachment, str] = {
    "cv": "CV",
    "cover_letter": "Cover letter",
    "portfolio": "Portfolio",
    "transcript": "Transcript",
    "references": "References",
    "work_samples": "Work samples",
}

_ATTACHMENT_PATTERNS: dict[Attachment, re.Pattern[str]] = {
    "cv": re.compile(r"\b(?:cv|resume|résumé|curriculum vitae)s?\b", re.I),
    "cover_letter": re.compile(r"\b(?:cover(?:ing)?|motivation(?:al)?) letters?\b", re.I),
    "portfolio": re.compile(r"\bportfolios?\b", re.I),
    "transcript": re.compile(r"\b(?:academic )?transcripts?\b", re.I),
    "references": re.compile(r"\b(?:references|referees|reference letters?)\b", re.I),
    "work_samples": re.compile(r"\b(?:work|writing|code|design) samples?\b", re.I),
}
_APPLY_CUE = re.compile(
    r"\b(?:apply|applying|application|send|submit|attach|include|email|e-mail|mail|share|provide|"
    r"forward|interested candidates?)\b",
    re.I,
)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Addresses that never receive applications.
_NOT_FOR_APPLICATIONS = re.compile(
    r"^(?:no-?reply|do-?not-?reply|privacy|dpo|gdpr|data-?protection|unsubscribe|abuse|"
    r"security|press|media|billing|accounts?)@",
    re.I,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class Classification:
    method: ApplyMethod
    apply_email: str | None
    apply_url: str | None
    attachments: tuple[Attachment, ...]


def _apply_sentences(description: str) -> list[str]:
    return [s for s in _SENTENCE.split(description) if _APPLY_CUE.search(s)]


def find_apply_email(description: str) -> str | None:
    for sentence in _apply_sentences(description):
        for match in _EMAIL.finditer(sentence):
            if not _NOT_FOR_APPLICATIONS.match(match.group(0)):
                return match.group(0).lower()
    return None


def required_attachments(description: str) -> tuple[Attachment, ...]:
    """The items the posting asks for. A CV is always required: every application carries one."""
    found: set[Attachment] = {"cv"}
    for sentence in _apply_sentences(description):
        for item, pattern in _ATTACHMENT_PATTERNS.items():
            if pattern.search(sentence):
                found.add(item)
    return tuple(item for item in ATTACHMENT_LABELS if item in found)


def classify(description: str, apply_url: str | None) -> Classification:
    email = find_apply_email(description)
    if email:
        method: ApplyMethod = "email"
    elif apply_url:
        method = "form"
    else:
        method = "unknown"
    return Classification(method, email, apply_url, required_attachments(description))
