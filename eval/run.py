"""Claim checker evaluation (spec section 15.2).

Every case is one tailored CV bullet that cites a line of the demo resume, labelled `true` (the
line supports everything it says) or `fabricated` (it plants something the user never showed:
an inflated number, a degree, an employer, a skill, a seniority, an injected statement). The real
Verifier (`app.services.verifier.verify`) checks each one against a ledger built the way ingestion
builds it: the code pass over the demo resume plus the facts an LLM proposes (eval/ledger.json),
each kept only if its cited line supports it.

The LLM claim pass is replayed from the claims recorded with each case, so the run is
deterministic and needs no API key. The code pass runs for real: a fabricated number, skill,
degree or employer is found even when the recorded claims leave it out.

A case is flagged when any of its claims is routed Needs you (or blocked). A fabrication counts
as caught only if a flagged claim overlaps the planted words. INFER_THRESHOLD is tuned on one half
of the cases (alternating within each label, in file order) and reported on the other half.

    python -m eval.run                  tune, report, print the confusion matrices
    python -m eval.run --show-misses    also list missed fabrications and false alarms
    python -m eval.run --check          exit 1 if the shipped threshold falls below the baseline
    python -m eval.run --write-baseline record the shipped threshold's numbers as the baseline
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import Settings
from app.domain.claims import Ledger, LedgerRow
from app.domain.cv import Block, SourceLine, base_blocks, plain
from app.domain.ledger import FactType, accept_proposed_facts, extract_facts_by_code, reconcile
from app.domain.resume import split_resume
from app.domain.taxonomy import Taxonomy, default_taxonomy
from app.integrations.embeddings import EmbeddingSimilarity, LexicalSimilarity, Similarity
from app.integrations.resume_text import extract_text
from app.services.verifier import Draft, verify

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent
CASES = EVAL_DIR / "claims.jsonl"
LEDGER = EVAL_DIR / "ledger.json"
BASELINE = EVAL_DIR / "baseline.json"

FLAGGED = frozenset({"needs_you", "blocked"})
THRESHOLDS = tuple(round(0.50 + 0.01 * i, 2) for i in range(46))  # 0.50 to 0.95
SHIPPED_THRESHOLD = float(Settings.model_fields["infer_threshold"].default)
EMBEDDING_MODEL = str(Settings.model_fields["embedding_model"].default)

Label = Literal["true", "fabricated"]
T = TypeVar("T", bound=BaseModel)


class RecordedClaim(BaseModel):
    """A claim the LLM pass returns: an exact quote from the bullet and its type."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str = Field(min_length=1)
    type: FactType


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    label: Label
    kind: str = Field(min_length=1)
    line: int
    text: str = Field(min_length=1)
    claims: tuple[RecordedClaim, ...] = ()
    planted: str | None = None

    @model_validator(mode="after")
    def _planted_words(self) -> Self:
        if self.label == "fabricated" and (not self.planted or self.planted not in self.text):
            raise ValueError("a fabricated case quotes its planted words from its text")
        if self.label == "true" and self.planted is not None:
            raise ValueError("a true case plants nothing")
        for claim in self.claims:
            if claim.text not in self.text:
                raise ValueError(f"recorded claim {claim.text!r} is not quoted from the text")
        return self


class ProposedFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: FactType
    text: str
    line: int


class LedgerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resume: str
    proposed_facts: list[ProposedFact]


class Baseline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold: float
    similarity: str
    catch_rate: float
    false_alarm_rate: float


@dataclass(frozen=True)
class World:
    bullets: Mapping[int, Block]  # tailorable resume lines by line number
    ledger: Ledger
    taxonomy: Taxonomy


@dataclass(frozen=True)
class Outcome:
    case: Case
    flagged: bool
    drafts: Sequence[Draft]


@dataclass(frozen=True)
class Scores:
    caught: int
    missed: int
    false_alarms: int
    passed: int

    @property
    def catch_rate(self) -> float:
        total = self.caught + self.missed
        return self.caught / total if total else 0.0

    @property
    def false_alarm_rate(self) -> float:
        total = self.false_alarms + self.passed
        return self.false_alarms / total if total else 0.0

    @property
    def youden(self) -> float:
        return self.catch_rate - self.false_alarm_rate


class ReplayedClaims:
    """Stands in for the LLM claim pass with the claims recorded for one case."""

    configured = True

    def __init__(self, claims: Iterable[RecordedClaim]) -> None:
        self.payload = {"claims": [{"block": 0, "text": c.text, "type": c.type} for c in claims]}

    async def generate(
        self, *, schema: type[T], system: str, task: str, data: Mapping[str, str]
    ) -> T:
        return schema.model_validate(self.payload)


class CachedSimilarity:
    """Scores each pair once; the threshold sweep asks for the same pairs many times."""

    def __init__(self, inner: Similarity) -> None:
        self.inner = inner
        self.cache: dict[tuple[str, str], float] = {}

    @property
    def method(self) -> str:
        return self.inner.method

    async def scores(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        missing = list(dict.fromkeys(p for p in pairs if p not in self.cache))
        if missing:
            self.cache.update(zip(missing, await self.inner.scores(missing), strict=True))
        return [self.cache[p] for p in pairs]


def load_cases(path: Path = CASES) -> list[Case]:
    cases = [
        Case.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("case ids must be unique")
    return cases


def build_world(taxonomy: Taxonomy, spec_path: Path = LEDGER) -> World:
    """The demo user's ledger, built as ingestion builds it (without the database)."""
    spec = LedgerSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    lines = split_resume(extract_text((ROOT / spec.resume).read_bytes(), "docx"))
    by_no = {line.line_no: line for line in lines}
    proposals = [(f.type, f.text, f.line) for f in spec.proposed_facts]
    accepted, rejected = accept_proposed_facts(proposals, by_no, taxonomy)
    if rejected:
        raise ValueError(f"{rejected} proposed ledger facts are not supported by their lines")
    facts = reconcile([*extract_facts_by_code(lines, taxonomy), *accepted], [], taxonomy)
    source = [SourceLine(ln.line_no, ln.line_no, ln.section, ln.text, ln.bulleted) for ln in lines]
    return World(
        bullets={b.line_no: b for b in base_blocks(source) if b.kind == "bullet"},
        ledger=Ledger.build(
            LedgerRow(f.type, f.normalized_value, f.claim_text, f.context) for f in facts
        ),
        taxonomy=taxonomy,
    )


def split(cases: Sequence[Case]) -> tuple[list[Case], list[Case]]:
    """(tune, test): alternating within each label in file order, so both halves see every kind."""
    tune: list[Case] = []
    test: list[Case] = []
    for label in ("true", "fabricated"):
        for i, case in enumerate(c for c in cases if c.label == label):
            (tune if i % 2 == 0 else test).append(case)
    return tune, test


def is_flagged(case: Case, text: str, drafts: Sequence[Draft]) -> bool:
    flagged = [d for d in drafts if d.bucket in FLAGGED]
    if case.label == "true" or case.planted is None:
        return bool(flagged)
    start = text.find(plain(case.planted))
    end = start + len(plain(case.planted))
    return any(
        d.bucket == "blocked" or (d.claim.start < end and start < d.claim.end) for d in flagged
    )


async def check(case: Case, world: World, similarity: Similarity, threshold: float) -> Outcome:
    source = world.bullets.get(case.line)
    if source is None:
        raise ValueError(f"case {case.id}: line {case.line} is not a tailorable bullet")
    block = replace(source, text=plain(case.text))
    result = await verify(
        [block],
        ledger=world.ledger,
        taxonomy=world.taxonomy,
        llm=ReplayedClaims(case.claims),
        similarity=similarity,
        threshold=threshold,
        max_questions=0,  # measure the checker itself, not the product's question limit
    )
    return Outcome(case, is_flagged(case, block.text, result.drafts), result.drafts)


async def run_all(
    cases: Sequence[Case], world: World, similarity: Similarity, threshold: float
) -> list[Outcome]:
    return [await check(c, world, similarity, threshold) for c in cases]


def score(outcomes: Iterable[Outcome]) -> Scores:
    outcomes = list(outcomes)
    fab = [o for o in outcomes if o.case.label == "fabricated"]
    true = [o for o in outcomes if o.case.label == "true"]
    caught = sum(o.flagged for o in fab)
    alarms = sum(o.flagged for o in true)
    return Scores(caught, len(fab) - caught, alarms, len(true) - alarms)


def best_threshold(results: Mapping[float, Scores]) -> float:
    """Highest catch rate minus false alarm rate; ties go to the higher (more cautious) value."""
    return max(results, key=lambda t: (round(results[t].youden, 9), t))


def matrix(scores: Scores) -> str:
    return "\n".join(
        [
            "                  flagged   passed",
            f"  fabricated   {scores.caught:>10} {scores.missed:>8}",
            f"  true         {scores.false_alarms:>10} {scores.passed:>8}",
            f"  Catch rate {scores.catch_rate:.1%} ({scores.caught}/{scores.caught + scores.missed})."
            f" False alarm rate {scores.false_alarm_rate:.1%}"
            f" ({scores.false_alarms}/{scores.false_alarms + scores.passed}).",
        ]
    )


def by_kind(outcomes: Sequence[Outcome]) -> str:
    rows = []
    for kind in dict.fromkeys(o.case.kind for o in outcomes):
        group = [o for o in outcomes if o.case.kind == kind]
        label = group[0].case.label
        good = (
            sum(o.flagged for o in group)
            if label == "fabricated"
            else sum(not o.flagged for o in group)
        )
        verb = "caught" if label == "fabricated" else "passed"
        rows.append(f"  {label:<10} {kind:<18} {good}/{len(group)} {verb}")
    return "\n".join(rows)


def misses(outcomes: Sequence[Outcome]) -> str:
    rows = []
    for o in outcomes:
        wrong = o.flagged if o.case.label == "true" else not o.flagged
        if not wrong:
            continue
        what = "false alarm" if o.case.label == "true" else "missed"
        rows.append(f"  {o.case.id} ({what}, {o.case.kind}): {o.case.text}")
        rows.extend(
            f"      {d.bucket:<9} {d.similarity:.2f}  {d.claim.type:<13} {d.claim.text}"
            for d in o.drafts
        )
    return "\n".join(rows) or "  none"


async def build_similarity(kind: str) -> Similarity:
    if kind == "lexical":
        return LexicalSimilarity()
    cache = os.environ.get("EMBEDDING_CACHE_DIR")
    embedding = EmbeddingSimilarity(EMBEDDING_MODEL, Path(cache) if cache else None)
    await embedding.load()
    if embedding.method != "embedding":
        raise SystemExit(f"The embedding model {EMBEDDING_MODEL} could not be loaded.")
    return embedding


async def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.run", description=__doc__.split("\n")[0])
    parser.add_argument("--similarity", choices=("embedding", "lexical"), default="embedding")
    parser.add_argument("--show-misses", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args(argv)

    cases = load_cases()
    world = build_world(default_taxonomy())
    similarity = CachedSimilarity(await build_similarity(args.similarity))
    tune, test = split(cases)
    fabricated = sum(c.label == "fabricated" for c in cases)

    sweep = {t: score(await run_all(tune, world, similarity, t)) for t in THRESHOLDS}
    tuned = best_threshold(sweep)
    held_out = await run_all(test, world, similarity, tuned)
    shipped = await run_all(cases, world, similarity, SHIPPED_THRESHOLD)
    shipped_scores = score(shipped)

    print("Power Hunt claim checker eval")
    print(
        f"Cases: {len(cases) - fabricated} true, {fabricated} fabricated"
        f" (tune {len(tune)}, held out {len(test)}). Similarity: {similarity.method}."
    )
    print(
        f"\nINFER_THRESHOLD tuned on the tune half: {tuned:.2f}"
        f" (catch rate {sweep[tuned].catch_rate:.1%}, false alarms {sweep[tuned].false_alarm_rate:.1%})."
    )
    print(f"\nHeld-out half at {tuned:.2f}:\n{matrix(score(held_out))}")
    print(
        f"\nAll cases at the shipped INFER_THRESHOLD {SHIPPED_THRESHOLD:.2f}:\n{matrix(shipped_scores)}"
    )
    print(f"\nBy kind, all cases at {SHIPPED_THRESHOLD:.2f}:\n{by_kind(shipped)}")
    if args.show_misses:
        print(f"\nMisses and false alarms at {SHIPPED_THRESHOLD:.2f}:\n{misses(shipped)}")

    current = Baseline(
        threshold=SHIPPED_THRESHOLD,
        similarity=similarity.method,
        catch_rate=round(shipped_scores.catch_rate, 4),
        false_alarm_rate=round(shipped_scores.false_alarm_rate, 4),
    )
    if args.write_baseline:
        BASELINE.write_text(current.model_dump_json(indent=2) + "\n", encoding="utf-8")
        print(f"\nBaseline written to {BASELINE.relative_to(ROOT)}.")
    if args.check:
        return compare(current, Baseline.model_validate_json(BASELINE.read_text(encoding="utf-8")))
    return 0


def compare(current: Baseline, baseline: Baseline) -> int:
    """0 when the current run is at least as good as the baseline, else 1 with the reasons."""
    problems = []
    if current.similarity != baseline.similarity:
        problems.append(
            f"similarity is {current.similarity}, the baseline used {baseline.similarity}"
        )
    if current.catch_rate < baseline.catch_rate:
        problems.append(f"catch rate {current.catch_rate:.1%} < baseline {baseline.catch_rate:.1%}")
    if current.false_alarm_rate > baseline.false_alarm_rate:
        problems.append(
            f"false alarm rate {current.false_alarm_rate:.1%} > baseline {baseline.false_alarm_rate:.1%}"
        )
    for problem in problems:
        print(f"Baseline check failed: {problem}.")
    if not problems:
        print("\nBaseline check passed.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
