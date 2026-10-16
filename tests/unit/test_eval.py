"""The claim eval's dataset and scoring (the eval itself runs as `python -m eval.run`)."""

import pytest
from pydantic import ValidationError

from app.domain.taxonomy import default_taxonomy
from app.integrations.embeddings import LexicalSimilarity
from eval.run import (
    Baseline,
    Case,
    Scores,
    best_threshold,
    build_world,
    check,
    compare,
    load_cases,
    split,
)

CASES = load_cases()
WORLD = build_world(default_taxonomy())


class TestDataset:
    def test_meets_the_spec_minimums(self) -> None:
        assert sum(c.label == "true" for c in CASES) >= 60
        assert sum(c.label == "fabricated" for c in CASES) >= 25

    def test_every_case_cites_a_tailorable_bullet(self) -> None:
        assert {c.line for c in CASES} <= set(WORLD.bullets)

    def test_the_ledger_is_built_from_the_demo_resume(self) -> None:
        assert WORLD.ledger.has("metric", "40%")
        assert WORLD.ledger.has("employer", "nimbus labs")
        assert not WORLD.ledger.has("skill", "kubernetes")

    def test_a_fabrication_must_quote_its_planted_words(self) -> None:
        with pytest.raises(ValidationError):
            Case(id="x", label="fabricated", kind="k", line=9, text="Cut cost", planted="45%")
        with pytest.raises(ValidationError):
            Case(id="x", label="true", kind="k", line=9, text="Cut cost", planted="cost")

    def test_halves_alternate_within_each_label(self) -> None:
        tune, test = split(CASES)
        for label in ("true", "fabricated"):
            assert (
                abs(sum(c.label == label for c in tune) - sum(c.label == label for c in test)) <= 1
            )


class TestScoring:
    async def test_a_planted_number_is_caught_and_the_true_line_passes(self) -> None:
        by_id = {c.id: c for c in CASES}
        similarity = LexicalSimilarity()
        assert (await check(by_id["f01"], WORLD, similarity, 0.85)).flagged
        assert not (await check(by_id["t03"], WORLD, similarity, 0.85)).flagged

    def test_rates(self) -> None:
        scores = Scores(caught=9, missed=1, false_alarms=2, passed=18)
        assert scores.catch_rate == 0.9
        assert scores.false_alarm_rate == 0.1

    def test_ties_go_to_the_more_cautious_threshold(self) -> None:
        same = Scores(9, 1, 1, 19)
        assert best_threshold({0.7: same, 0.8: same, 0.9: Scores(10, 0, 5, 15)}) == 0.8

    def test_baseline_check(self) -> None:
        base = Baseline(
            threshold=0.85, similarity="embedding", catch_rate=0.9, false_alarm_rate=0.05
        )
        assert compare(base, base) == 0
        assert compare(base.model_copy(update={"catch_rate": 0.89}), base) == 1
        assert compare(base.model_copy(update={"false_alarm_rate": 0.06}), base) == 1
        assert compare(base.model_copy(update={"similarity": "lexical"}), base) == 1
