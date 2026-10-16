"""Skill taxonomy: canonical names, synonyms and whole-word matching in free text.

Matching rules, chosen to keep false positives out of the ledger and the ATS score:
- Case-insensitive by default.
- A skill marked `case_sensitive`, and any term of three characters or fewer, must appear exactly
  as written ("Go", "ML", "AWS"), so "go to market" or "html" in a URL slug do not count.
- Short terms may not be followed by "-word" ("Go-to-market").
- Single letters ("C", "R") only count inside a list ("Python, C, Java") or as "C programming".
"""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_TAXONOMY_PATH = Path(__file__).resolve().parents[2] / "data" / "skills_taxonomy.json"

SHORT_TERM_MAX_LEN = 3
_LEFT = r"(?<![\w+#.])"
_RIGHT = r"(?![\w+#]|\.\w)"
_RIGHT_SHORT = r"(?![\w+#]|\.\w|-\w)"


@dataclass(frozen=True)
class Skill:
    name: str
    category: str
    aliases: tuple[str, ...]
    case_sensitive: bool


@dataclass(frozen=True)
class SkillMatch:
    canonical: str
    surface: str
    start: int
    end: int


def _alternation(terms: list[str]) -> str:
    # Longest first so "Node.js" wins over "Node" and "C++" over "C".
    return "|".join(re.escape(t) for t in sorted(set(terms), key=len, reverse=True))


class Taxonomy:
    def __init__(self, skills: list[Skill]) -> None:
        self.skills = {s.name: s for s in skills}
        self._insensitive: dict[str, str] = {}
        self._sensitive: dict[str, str] = {}
        self._single: dict[str, str] = {}

        for skill in skills:
            for term in (skill.name, *skill.aliases):
                term = term.strip()
                if not term:
                    continue
                if len(term) == 1:
                    self._single[term] = skill.name
                elif skill.case_sensitive or len(term) <= SHORT_TERM_MAX_LEN:
                    self._sensitive[term] = skill.name
                else:
                    self._insensitive[term.lower()] = skill.name

        long_sensitive = [t for t in self._sensitive if len(t) > SHORT_TERM_MAX_LEN]
        short_sensitive = [t for t in self._sensitive if len(t) <= SHORT_TERM_MAX_LEN]
        self._patterns: list[re.Pattern[str]] = []
        if self._insensitive:
            self._patterns.append(
                re.compile(_LEFT + f"(?:{_alternation(list(self._insensitive))})" + _RIGHT, re.I)
            )
        if long_sensitive:
            self._patterns.append(
                re.compile(_LEFT + f"(?:{_alternation(long_sensitive)})" + _RIGHT)
            )
        if short_sensitive:
            self._patterns.append(
                re.compile(_LEFT + f"(?:{_alternation(short_sensitive)})" + _RIGHT_SHORT)
            )
        if self._single:
            letters = "".join(re.escape(t) for t in self._single)
            # Either inside a list ("Python, C, Java") or named explicitly ("C programming").
            self._patterns.append(
                re.compile(
                    rf"(?:^|(?<=[,(/|:;]))\s*([{letters}])(?=\s*(?:[,)/|;]|$))"
                    rf"|(?<![\w+#.])([{letters}])(?=\s+(?:programming|language)\b)",
                    re.M,
                )
            )

    @classmethod
    def from_file(cls, path: Path = DEFAULT_TAXONOMY_PATH) -> "Taxonomy":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            [
                Skill(
                    name=s["name"],
                    category=s.get("category", "other"),
                    aliases=tuple(s.get("aliases", [])),
                    case_sensitive=bool(s.get("case_sensitive", False)),
                )
                for s in raw["skills"]
            ]
        )

    def _lookup(self, surface: str) -> str | None:
        if surface in self._single:
            return self._single[surface]
        if surface in self._sensitive:
            return self._sensitive[surface]
        return self._insensitive.get(surface.lower())

    def canonical(self, term: str) -> str | None:
        """Canonical skill name for an exact term (an alias or the name itself), else None."""
        term = term.strip()
        found = self._lookup(term)
        if found:
            return found
        # Accept a case variant of a case-sensitive long name (e.g. "react" typed in a form)
        # only for explicit lookups, never for free-text scanning.
        for key, name in self._sensitive.items():
            if len(key) > SHORT_TERM_MAX_LEN and key.lower() == term.lower():
                return name
        return None

    def find(self, text: str) -> list[SkillMatch]:
        """All non-overlapping skill mentions in `text`, longest match winning, in text order."""
        candidates: list[SkillMatch] = []
        for pattern in self._patterns:
            for m in pattern.finditer(text):
                group = next((i for i in range(1, pattern.groups + 1) if m.group(i)), 0)
                surface = m.group(group)
                canonical = self._lookup(surface)
                if canonical:
                    candidates.append(SkillMatch(canonical, surface, m.start(group), m.end(group)))
        candidates.sort(key=lambda c: (c.start, -(c.end - c.start)))
        result: list[SkillMatch] = []
        last_end = -1
        for c in candidates:
            if c.start >= last_end:
                result.append(c)
                last_end = c.end
        return result

    def skills_in(self, text: str) -> set[str]:
        return {m.canonical for m in self.find(text)}


@lru_cache
def default_taxonomy() -> Taxonomy:
    return Taxonomy.from_file()
