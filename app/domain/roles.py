"""Target role titles: accept real job titles, reject noise, and suggest common titles.

A job title is built around a head noun ("engineer", "scientist", "analyst", "intern"). Requiring
one rejects "bla bla bla", sentences and pasted junk while still accepting uncommon but real
titles such as "Bioinformatics Scientist" or "Quantum Computing Researcher".
"""

import json
import re
from functools import lru_cache
from pathlib import Path

DEFAULT_TITLES_PATH = Path(__file__).resolve().parents[2] / "data" / "job_titles.json"

HEAD_NOUNS = frozenset(
    [
        "engineer",
        "developer",
        "programmer",
        "coder",
        "scientist",
        "analyst",
        "architect",
        "manager",
        "lead",
        "head",
        "director",
        "designer",
        "researcher",
        "consultant",
        "specialist",
        "administrator",
        "admin",
        "officer",
        "associate",
        "intern",
        "internship",
        "trainee",
        "apprentice",
        "technician",
        "tester",
        "coordinator",
        "strategist",
        "writer",
        "editor",
        "marketer",
        "recruiter",
        "accountant",
        "auditor",
        "advisor",
        "adviser",
        "executive",
        "assistant",
        "representative",
        "operator",
        "instructor",
        "teacher",
        "lecturer",
        "professor",
        "tutor",
        "fellow",
        "owner",
        "master",
        "evangelist",
        "advocate",
        "statistician",
        "economist",
        "mathematician",
        "physicist",
        "chemist",
        "biologist",
        "technologist",
        "practitioner",
        "expert",
        "generalist",
        "pentester",
        "steward",
        "planner",
        "agent",
        "clerk",
        "producer",
        "artist",
        "illustrator",
        "animator",
        "photographer",
        "modeler",
        "modeller",
        "translator",
        "founder",
        "cofounder",
        "partner",
        "president",
        "vp",
        "cto",
        "ceo",
        "cfo",
        "coo",
        "cio",
        "ciso",
        "sre",
        "devops",
        "qa",
        "mentor",
        "educator",
        "trainer",
        "supervisor",
        "superintendent",
        "inspector",
        "investigator",
        "controller",
        "officer",
        "reviewer",
        "curator",
        "librarian",
        "archivist",
        "ambassador",
        "champion",
    ]
)
ALLOWED_CHARS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &/+#.,()'\-]*$")
MIN_LENGTH, MAX_LENGTH, MAX_WORDS = 2, 80, 10


class RoleRejectedError(ValueError):
    """The text is not a usable job title. The message is safe to show the user."""


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[\s/&,()+\-]+", text.lower()) if w]


def _singular(word: str) -> str:
    return word[:-1] if word.endswith("s") and word[:-1] in HEAD_NOUNS else word


def normalize_role(text: str) -> str:
    """The cleaned title, or RoleRejectedError explaining what is wrong."""
    title = re.sub(r"\s+", " ", text).strip(" ,.-")
    if not MIN_LENGTH <= len(title) <= MAX_LENGTH:
        raise RoleRejectedError(f"A role title should be {MIN_LENGTH} to {MAX_LENGTH} characters.")
    if re.search(r"https?://|www\.|@", title, re.I):
        raise RoleRejectedError("A role title can't contain links or email addresses.")
    if not ALLOWED_CHARS.match(title):
        raise RoleRejectedError(
            "A role title can only use letters, numbers and simple punctuation."
        )
    words = _words(title)
    if len(words) > MAX_WORDS:
        raise RoleRejectedError(f"Keep the role title to {MAX_WORDS} words or fewer.")
    if any(words.count(w) >= 3 for w in words):
        raise RoleRejectedError("That doesn't look like a job title.")
    if not any(_singular(w) in HEAD_NOUNS for w in words):
        raise RoleRejectedError(
            f"'{title}' doesn't look like a job title. Try something like 'Data Analyst' or 'ML Engineer'."
        )
    return title


@lru_cache(maxsize=1)
def common_titles(path: Path = DEFAULT_TITLES_PATH) -> tuple[str, ...]:
    titles: list[str] = json.loads(path.read_text(encoding="utf-8"))["titles"]
    return tuple(titles)


def suggest_roles(text: str, limit: int = 10) -> list[str]:
    """Common titles containing every typed word as a word prefix, shortest first."""
    query = _words(text)
    if not query:
        return []
    matches = [
        title
        for title in common_titles()
        if all(any(w.startswith(q) for w in _words(title)) for q in query)
    ]
    return sorted(matches, key=lambda t: (not t.lower().startswith(text.strip().lower()), len(t)))[
        :limit
    ]
