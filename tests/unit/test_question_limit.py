"""The Needs-you question limit: past it, a rewrite goes back to the user's own words. An unproven
claim is asked about or removed, never relabelled as proven."""

from app.domain.claims import FoundClaim
from app.domain.cv import Block
from app.services.verifier import Draft, _over_limit


def _block(i: int, rewritten: bool) -> Block:
    original = f"Original line {i}"
    return Block(
        "bullet", "experience", original + (" rewritten" if rewritten else ""), i, i, 1, original
    )


def _asks(i: int, kinds: list[str]) -> list[Draft]:
    return [
        Draft(i, FoundClaim(kind, kind, 0, 1, "code"), "needs_you", kind, 0.5, i)  # type: ignore[arg-type]
        for kind in kinds
    ]


def test_within_the_limit_nothing_is_restored() -> None:
    blocks = [_block(0, True), _block(1, True)]
    assert _over_limit({0: _asks(0, ["metric"]), 1: _asks(1, ["skill", "skill"])}, blocks, 3) == []


def test_the_riskiest_rewrites_are_asked_and_the_rest_restored() -> None:
    blocks = [_block(0, True), _block(1, True), _block(2, True)]
    per_block = {
        0: _asks(0, ["skill", "skill"]),
        1: _asks(1, ["metric"]),  # a number is the riskiest: asked first
        2: _asks(2, ["degree", "skill"]),
    }
    # Asked: the number (1), then the degree bullet (3 in all); the skill-only rewrite goes back.
    assert _over_limit(per_block, blocks, 3) == [0]


def test_the_users_own_lines_are_never_restored_and_count_first() -> None:
    blocks = [_block(0, False), _block(1, True)]
    per_block = {0: _asks(0, ["metric", "metric", "metric"]), 1: _asks(1, ["skill"])}
    # Line 0 is already the user's words; the rewrite still gets its one question asked first.
    assert _over_limit(per_block, blocks, 3) == []
    blocks.append(_block(2, True))
    per_block[2] = _asks(2, ["skill"])
    assert _over_limit(per_block, blocks, 3) == [2]


def test_zero_means_ask_about_everything() -> None:
    blocks = [_block(0, True)]
    assert _over_limit({0: _asks(0, ["skill"] * 9)}, blocks, 0) == []
