"""Gate 2 edits on overlapping claims: changing part of a statement never lets the rest through."""

from datetime import UTC, datetime

from app.db.models import Claim
from app.domain.cv import Block
from app.services.verifier import remove

NOW = datetime(2026, 10, 17, tzinfo=UTC)
TEXT = "Senior backend and AI engineer who ships Python services"


def _claim(id_: int, quote: str, kind: str, bucket: str) -> Claim:
    start = TEXT.index(quote)
    return Claim(
        id=id_,
        block_index=0,
        text=quote,
        type=kind,
        normalized_value=quote.lower(),
        span_start=start,
        span_end=start + len(quote),
        bucket=bucket,
    )


def _setup() -> tuple[list[Block], Claim, Claim, Claim]:
    blocks = [Block("bullet", "summary", TEXT, 1, 5, 1, "Backend and AI engineer")]
    role = _claim(1, "Senior backend and AI engineer", "role", "needs_you")
    skill = _claim(2, "AI", "skill", "verified")
    python = _claim(3, "Python", "skill", "verified")
    return blocks, role, skill, python


class TestOverlappingClaims:
    def test_removing_an_inner_claim_reopens_the_statement_around_it(self) -> None:
        blocks, role, skill, python = _setup()
        role.bucket = "verified"  # even an accepted statement must be looked at again
        remove(blocks, skill, [role, skill, python], now=NOW, decision="removed")
        assert skill.bucket == "removed"
        assert role.bucket == "needs_you"
        assert role.text == "Senior backend and engineer"
        assert blocks[0].text[role.span_start : role.span_end] == role.text
        assert python.bucket == "verified"
        assert blocks[0].text[python.span_start : python.span_end] == "Python"

    def test_removing_the_outer_claim_removes_what_it_contained(self) -> None:
        blocks, role, skill, python = _setup()
        remove(blocks, role, [role, skill, python], now=NOW, decision="removed")
        assert role.bucket == skill.bucket == "removed"
        assert blocks[0].text.strip().startswith("who ships Python")
        assert blocks[0].text[python.span_start : python.span_end] == "Python"
