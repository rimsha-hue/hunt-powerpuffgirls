"""Matcher: a 0-100 fit score for a job and the skills it needs that the ledger can't support.

Fit = 60% skills (the job's skill keywords, weighted, that the ledger proves) + 30% role (how well
the title matches a target role) + 10% place. Only ledger evidence counts, so the score can't be
raised by anything the user hasn't shown.

Skill fit is smoothed toward a neutral 0.5 by a small prior weight, so a short posting that names
one skill the user has reads as a decent match, not a perfect one; long postings barely move.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.domain.ats import Keyword

SKILL_SHARE = 0.6
ROLE_SHARE = 0.3
PLACE_SHARE = 0.1
# Pseudo-keyword weight at a neutral 50% fit, added to every posting's skill keywords.
SKILL_PRIOR_WEIGHT = 2
NEUTRAL_FIT = 0.5


@dataclass(frozen=True)
class MatchResult:
    score: int
    matched_skills: list[str]
    missing_skills: list[str]


def match_job(
    keywords: Sequence[Keyword],
    ledger_skills: Iterable[str],
    role_relevance: float,
    place_fits: bool,
) -> MatchResult:
    """`ledger_skills` are the normalised values of the user's skill evidence (lowercase)."""
    proven = set(ledger_skills)
    skills = [k for k in keywords if k.kind == "skill"]
    matched = [k for k in skills if k.key in proven]
    # Required skills first, so the most important gaps lead the list.
    missing = sorted((k for k in skills if k.key not in proven), key=lambda k: -k.weight)
    total = sum(k.weight for k in skills)
    proven_weight = sum(k.weight for k in matched)
    # A posting that names no known skill is judged on its title alone.
    skill_fit = (
        (proven_weight + SKILL_PRIOR_WEIGHT * NEUTRAL_FIT) / (total + SKILL_PRIOR_WEIGHT)
        if total
        else role_relevance
    )
    fit = SKILL_SHARE * skill_fit + ROLE_SHARE * role_relevance + PLACE_SHARE * float(place_fits)
    return MatchResult(
        score=round(100 * fit),
        matched_skills=[k.term for k in matched],
        missing_skills=[k.term for k in missing],
    )
