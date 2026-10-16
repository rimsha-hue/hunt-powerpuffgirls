"""Form questions (spec Flow D): one shape for every source, a type for each question, and the
answers code can give on its own.

Types: factual_profile (name, contact, work authorization...), factual_ledger (degree, GPA),
descriptive (why us, tell us), analytical (estimate, approach), file (CV, cover letter). Two more
keep the human in charge: consent (legal agreements and policies, always answered by the user and
never remembered) and voluntary (EEO questions, "I don't wish to answer" until the user says
otherwise).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from app.domain.locations import fold

QuestionType = Literal[
    "factual_profile", "factual_ledger", "descriptive", "analytical", "file", "consent", "voluntary"
]
FieldType = Literal["text", "textarea", "select", "multiselect", "file", "checkbox"]

# Spec section 8: form answers are 60 to 150 words unless the question sets its own limit.
ANSWER_MIN_WORDS = 60
ANSWER_MAX_WORDS = 150
MAX_LABEL = 300
MAX_DESCRIPTION = 1500
MAX_QUESTIONS = 60


@dataclass(frozen=True)
class Question:
    label: str
    field_type: FieldType
    required: bool = False
    description: str = ""
    options: tuple[str, ...] = ()
    name: str = ""  # the form field's own name, for the Apply Pack and autofill

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["options"] = list(self.options)
        return data


def clean_question(
    label: str,
    field_type: FieldType,
    *,
    required: bool = False,
    description: str = "",
    options: Sequence[str] = (),
    name: str = "",
) -> Question | None:
    label = " ".join(label.split()).strip(" :*")[:MAX_LABEL]
    if not label:
        return None
    return Question(
        label=label,
        field_type=field_type,
        required=required,
        description=" ".join(description.split())[:MAX_DESCRIPTION],
        options=tuple(o.strip() for o in options if o.strip())[:50],
        name=name[:200],
    )


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, re.I) is not None


_CONSENT = r"\b(?:arbitrat\w*|i agree|agree to|consent|acknowledg\w*|certify|attest|terms (?:and|&) conditions|privacy (?:notice|policy)|ai policy|policy for application)\b"
_VOLUNTARY = r"\b(?:gender|race|ethnicity|hispanic|latino|veteran\w*|disabilit\w*|sexual orientation|transgender|pronouns?)\b|veteranstatus|disabilitystatus"
_PROFILE_KEYS: Sequence[tuple[str, str]] = (
    (r"^first name$|\bgiven name\b", "first_name"),
    (r"^last name$|\b(?:family name|surname)\b", "last_name"),
    (r"^(?:full )?name$|\byour (?:full )?name\b", "full_name"),
    (r"\be-?mail\b", "email"),
    (r"\b(?:phone|mobile|cell)\b", "phone"),
    (r"\blinkedin\b", "linkedin"),
    (r"\bgithub\b", "github"),
    (r"\b(?:website|portfolio|personal (?:site|url))\b", "website"),
    (r"\b(?:sponsor\w*|visa)\b", "sponsorship"),
    (
        r"\b(?:authori[sz]ed to work|work authori[sz]ation|right to work|eligible to work)\b",
        "work_authorization",
    ),
    (
        r"\b(?:notice period|earliest .*start|start date|when can you start|available to start)\b",
        "notice_period",
    ),
    (
        r"\b(?:salary|compensation|pay) (?:expectation|requirement|range)s?\b|\bexpected (?:salary|compensation)\b",
        "salary",
    ),
    (
        r"^(?:current )?(?:location|city)\b|\bwhere are you (?:based|located)\b|\bcountry of residence\b",
        "location",
    ),
)
_LEDGER = (
    r"\b(?:degree|education|university|college|school|gpa|grade point|cgpa|highest level of)\b"
)
_ANALYTICAL = r"\b(?:how would you|walk (?:us|me) through|estimate|how many|calculate|design (?:a|an)|what would you do|approach (?:to|this)|case study|trade-?offs?)\b"
_DESCRIPTIVE = r"\b(?:why|tell us|describe|share|what (?:excites|interests|motivates|draws)|about yourself|cover letter|additional information|anything else|challenge|proud|example of)\b"


def classify(question: Question) -> tuple[QuestionType, str | None]:
    """(type, profile or ledger key) for a question, from its label, description and field."""
    label = question.label.lower()
    text = f"{label} {question.description.lower()}"
    if question.field_type == "file":
        return "file", "cover_letter" if "cover" in label else "cv"
    if _has(text, _CONSENT):
        return "consent", None
    if _has(label, _VOLUNTARY):
        return "voluntary", None
    for pattern, key in _PROFILE_KEYS:
        if _has(label, pattern):
            return "factual_profile", key
    if _has(label, _LEDGER):
        return "factual_ledger", "gpa" if _has(label, r"\b(?:gpa|grade point|cgpa)\b") else "degree"
    if _has(text, _ANALYTICAL):
        return "analytical", None
    if question.field_type == "textarea" or _has(label, _DESCRIPTIVE):
        return "descriptive", None
    return "factual_profile", None  # a short question the profile can't answer: ask once


def word_limit(question: Question) -> tuple[int, int, int | None]:
    """(min words, max words, max characters) for a written answer. The question's own limit
    always wins over the default range."""
    text = f"{question.label} {question.description}"
    span = re.search(r"(\d{2,4})\s*(?:-|–|to)\s*(\d{2,4})\s*words?", text, re.I)
    if span:
        low, high = sorted((int(span.group(1)), int(span.group(2))))
        return low, high, None
    words = re.search(r"(\d{2,4})\s*words?", text, re.I)
    chars = re.search(r"(\d{2,5})\s*char", text, re.I)
    max_chars = int(chars.group(1)) if chars else None
    if words:
        limit = int(words.group(1))
        return min(ANSWER_MIN_WORDS, limit // 2), limit, max_chars
    if max_chars:
        approx = max(20, max_chars // 7)
        return min(ANSWER_MIN_WORDS, approx // 2), min(ANSWER_MAX_WORDS, approx), max_chars
    return ANSWER_MIN_WORDS, ANSWER_MAX_WORDS, None


def saved_answer_key(label: str) -> str:
    return fold(label)[:200]


_DECLINE = r"\b(?:decline|don'?t wish|do not wish|prefer not|choose not)\b"
_YES = {"yes", "y", "true"}
_NO = {"no", "n", "false"}


def pick_option(answer: str, options: Sequence[str]) -> str | None:
    """The option matching an answer: exact, then case-insensitive, then yes/no by meaning."""
    if not options:
        return answer
    if answer in options:
        return answer
    lowered = {o.lower(): o for o in options}
    if answer.lower() in lowered:
        return lowered[answer.lower()]
    word = answer.strip().lower()
    for option in options:
        first = option.strip().lower().split(" ")[0].strip(",.")
        if (word in _YES and first in _YES) or (word in _NO and first in _NO):
            return option
    return None


def decline_option(options: Sequence[str]) -> str | None:
    return next((o for o in options if _has(o, _DECLINE)), None)


@dataclass(frozen=True)
class ProfileView:
    """What the profile can answer, already formatted for a form."""

    first_name: str = ""
    last_name: str = ""
    full_name: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""
    location: str = ""
    work_authorization: str = ""  # a WorkAuthorization value
    work_authorization_label: str = ""
    notice_period: str = ""  # label
    salary: str = ""
    saved: Mapping[str, str] = field(default_factory=dict)


def profile_answer(key: str | None, question: Question, view: ProfileView) -> str | None:
    """The profile's answer to a factual question, fitted to the question's options; None if the
    profile can't answer it (the user is asked)."""
    if key is None:
        value = view.saved.get(saved_answer_key(question.label))
    elif key == "sponsorship":
        value = {"needs_sponsorship": "Yes", "citizen": "No", "permanent_resident": "No"}.get(
            view.work_authorization
        )
    elif key == "work_authorization":
        if question.options:
            value = {
                "citizen": "Yes",
                "permanent_resident": "Yes",
                "work_permit": "Yes",
                "needs_sponsorship": "No",
            }.get(view.work_authorization)
        else:
            value = view.work_authorization_label or None
    else:
        value = getattr(view, key, "") or None
    if not value:
        return None
    return pick_option(value, question.options)


def render_analytical(
    assumptions: Sequence[str], steps: Sequence[str], result: str, risk: str
) -> str:
    """Spec Flow D: assumptions, an approach in two to four steps, the result, one risk."""
    lines = [
        "Assumptions: " + " ".join(a.strip().rstrip(".") + "." for a in assumptions if a.strip())
    ]
    lines.append("Approach:")
    lines += [f"{i}. {step.strip()}" for i, step in enumerate(steps, start=1)]
    lines.append(f"Result: {result.strip()}")
    lines.append(f"Risk: {risk.strip()}")
    return "\n".join(lines)
