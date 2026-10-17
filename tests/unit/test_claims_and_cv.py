import random
from dataclasses import replace

import pytest

from app.domain.ats import CvDocument, Keyword, score_cv, supported_keys
from app.domain.claims import (
    FoundClaim,
    Ledger,
    clause_around,
    code_claims,
    locate,
    merge,
    outside,
    route,
)
from app.domain.cv import SourceLine, apply_rewrites, base_blocks, cv_document, replace_span
from app.domain.cv_format import (
    CV_TEMPLATES,
    MAX_CV_PAGES,
    CvTemplate,
    format_checks,
    passes,
    split_dates,
)
from app.domain.style import style_problems
from app.domain.taxonomy import default_taxonomy
from app.integrations.embeddings import lexical_similarity
from app.integrations.pdf import checked_cv, read_back, render_cv

TAX = default_taxonomy()

LINES = [
    SourceLine(101, 1, "header", "AYESHA KHAN", False),
    SourceLine(104, 4, "experience", "EXPERIENCE", False),
    SourceLine(
        105, 5, "experience", "Machine Learning Engineer, Nimbus Labs, Jul 2023 - Present", False
    ),
    SourceLine(
        106, 6, "experience", "Reduced LLM inference cost by 40% by adding Redis caching", True
    ),
    SourceLine(107, 7, "experience", "Shipped FastAPI services on Docker", True),
    SourceLine(
        108, 8, "experience", "Software Engineer Intern, Acme Analytics, Jun 2022 - Aug 2022", False
    ),
    SourceLine(109, 9, "experience", "Automated ETL pipelines in Apache Airflow", True),
    SourceLine(110, 10, "education", "EDUCATION", False),
    SourceLine(111, 11, "education", "BS Computer Science, NUST, 2019 - 2023", False),
]
LEDGER = Ledger.build(
    [
        ("metric", "40%"),
        ("skill", "redis"),
        ("skill", "fastapi"),
        ("skill", "docker"),
        ("employer", "nimbus labs"),
        ("degree", "bachelor|computer science"),
    ]
)


def _route(text: str, claim: FoundClaim, line: str, sim: float = 0.9) -> str:
    return route(
        claim, ledger=LEDGER, taxonomy=TAX, source_line=line, similarity=sim, threshold=0.78
    ).bucket


def _claim(text: str, quote: str, kind: str) -> FoundClaim:
    span = locate(text, quote)
    assert span
    return FoundClaim(text[span[0] : span[1]], kind, span[0], span[1], "llm")  # type: ignore[arg-type]


class TestBlocks:
    def test_kinds_and_entries(self) -> None:
        blocks = base_blocks(LINES)
        kinds = {b.line_id: b.kind for b in blocks}
        assert kinds[105] == kinds[108] == "title"
        assert kinds[106] == kinds[109] == "bullet"
        assert kinds[111] == "text"  # education stays word for word
        by_line = {b.line_id: b for b in blocks}
        assert by_line[106].entry == by_line[107].entry != by_line[109].entry

    def test_rewrites_reorder_only_within_an_entry(self) -> None:
        blocks, rejected = apply_rewrites(
            base_blocks(LINES),
            [
                (109, "Built Airflow ETL pipelines"),
                (107, "Shipped FastAPI on Docker"),
                (105, "Lead"),
            ],
        )
        order = [b.line_id for b in blocks]
        assert order.index(107) < order.index(106)  # moved up within Nimbus Labs
        assert order.index(108) < order.index(109)  # Acme's bullet stays under Acme
        assert rejected == 1  # a title can't be rewritten
        assert next(b for b in blocks if b.line_id == 105).text.startswith("Machine Learning")

    def test_typographic_hyphens_become_plain(self) -> None:
        blocks, _ = apply_rewrites(
            base_blocks(LINES),
            [(107, "Shipped FastAPI\u2011based services\u00a0on Docker, 3\u00d7 faster")],
        )
        assert (
            next(b for b in blocks if b.line_id == 107).text
            == "Shipped FastAPI-based services on Docker, 3x faster"
        )

    def test_a_line_can_only_be_rewritten_once(self) -> None:
        _, rejected = apply_rewrites(base_blocks(LINES), [(106, "One"), (106, "Two")])
        assert rejected == 1

    def test_removal_tidies_the_sentence(self) -> None:
        text = "Built APIs, cut latency by 30%, and shipped weekly"
        assert replace_span(text, 10, 31, "") == "Built APIs and shipped weekly"
        assert replace_span("Used Go, Redis, and SQL", 9, 14, "") == "Used Go, and SQL"


class TestCodePass:
    def test_finds_numbers_skills_degrees_and_employers(self) -> None:
        text = "Led 5 engineers at Google building Kubernetes tooling with a PhD in Physics, cut cost 35%"
        found = {(c.type, c.text) for c in code_claims(text, TAX, {"employer": ["Nimbus Labs"]})}
        assert ("metric", "35%") in found
        assert ("skill", "Kubernetes") in found
        assert ("employer", "Google") in found
        assert any(t == "degree" for t, _ in found)
        assert any(t == "metric" and "5" in s for t, s in found)

    def test_version_numbers_are_not_claims(self) -> None:
        assert not [
            c for c in code_claims("Fine-tuned GPT-4 models", TAX, {}) if c.type == "metric"
        ]

    def test_an_llm_claim_the_code_claims_cover_is_dropped(self) -> None:
        code = FoundClaim("40%", "metric", 10, 13, "code")
        same = FoundClaim("40%", "metric", 10, 13, "llm")
        other = FoundClaim("Redis", "skill", 20, 25, "llm")
        assert merge([same, code, other]) == [code, other]

    def test_an_llm_claim_that_says_more_is_kept_beside_the_code_claim(self) -> None:
        text = "Senior backend and AI engineer"
        code = next(c for c in code_claims(text, TAX, {}) if c.text == "AI")
        role = FoundClaim(text, "role", 0, len(text), "llm")
        assert merge([code, role]) == [role, code]

    def test_overlapping_llm_claims_keep_the_earlier(self) -> None:
        first = FoundClaim("Led a team", "role", 0, 10, "llm")
        second = FoundClaim("team of engineers", "role", 6, 23, "llm")
        assert merge([second, first]) == [first]


class TestRouting:
    LINE = "Reduced LLM inference cost by 40% by adding Redis caching"

    def test_exact_ledger_matches_are_verified(self) -> None:
        text = "Cut LLM inference cost 40% with Redis caching"
        claims = {c.text: c for c in code_claims(text, TAX, {})}
        assert _route(text, claims["40%"], self.LINE) == "verified"
        assert _route(text, claims["Redis"], self.LINE) == "verified"

    def test_an_inflated_number_needs_you(self) -> None:
        text = "Cut LLM inference cost 45% with Redis caching"
        claim = next(c for c in code_claims(text, TAX, {}) if c.type == "metric")
        assert _route(text, claim, self.LINE) == "needs_you"

    def test_a_skill_the_line_lacks_needs_you(self) -> None:
        text = "Cut LLM cost 40% with Redis and Kubernetes"
        claim = next(c for c in code_claims(text, TAX, {}) if c.text == "Kubernetes")
        assert _route(text, claim, self.LINE) == "needs_you"

    def test_fake_degree_and_employer_need_you(self) -> None:
        text = "PhD in Physics from MIT, worked at Google"
        for claim in code_claims(text, TAX, {}):
            if claim.type in ("degree", "employer"):
                assert _route(text, claim, self.LINE) == "needs_you"

    def test_paraphrase_of_the_line_is_inferred(self) -> None:
        text = "Lowered LLM inference spend through response caching"
        claim = _claim(text, "Lowered LLM inference spend", "project")
        assert _route(text, claim, self.LINE, sim=0.85) == "inferred"
        assert _route(text, claim, self.LINE, sim=0.6) == "needs_you"
        assert _route(text, claim, None, sim=0.95) == "needs_you"  # no source line

    def test_words_from_the_line_ground_an_unknown_skill(self) -> None:
        line = "Reduced cost by adding Redis caching and prompt compression"
        text = "Cut cost with prompt compression"
        claim = _claim(text, "prompt compression", "skill")
        assert _route(text, claim, line, sim=0.9) == "inferred"
        assert (
            _route(text, _claim("Used vector quantization", "vector quantization", "skill"), line)
            == "needs_you"
        )

    def test_a_title_the_line_lacks_needs_you(self) -> None:
        for quote, kind in [("As tech lead", "role"), ("Google Developer Expert", "certification")]:
            text = f"{quote}, reduced LLM inference cost by 40% with Redis caching"
            assert _route(text, _claim(text, quote, kind), self.LINE, sim=0.97) == "needs_you"

    def test_a_title_the_line_states_is_inferred(self) -> None:
        line = "Backend and AI engineer who builds reliable Python services"
        text = "AI and backend engineer who builds Python services"
        claim = _claim(text, "AI and backend engineer", "role")
        assert _route(text, claim, line, sim=0.9) == "inferred"

    def test_ledger_proven_skills_ground_a_paraphrase(self) -> None:
        line = "Shipped FastAPI services on Kubernetes"
        text = "Shipped Docker FastAPI services"
        claim = _claim(text, "Shipped Docker FastAPI services", "project")
        assert _route(text, claim, line, sim=0.9) == "inferred"  # Docker is in the ledger
        text = "Shipped Terraform FastAPI services"
        claim = _claim(text, "Shipped Terraform FastAPI services", "project")
        assert _route(text, claim, line, sim=0.9) == "needs_you"

    def test_blocklisted_values_are_blocked(self) -> None:
        ledger = Ledger.build([], blocklist=["45%"])
        claim = FoundClaim("45%", "metric", 0, 3, "code")
        routing = route(
            claim, ledger=ledger, taxonomy=TAX, source_line=None, similarity=None, threshold=0.78
        )
        assert routing.bucket == "blocked"

    def test_metric_with_several_numbers_needs_each(self) -> None:
        assert Ledger.build([("metric", "40%|1200 user")]).has("metric", "40%")
        assert not Ledger.build([("metric", "40%")]).has("metric", "40%|3x")


class TestAtsAfter:
    KEYWORDS = [
        Keyword("Python", "python", "skill", 2),
        Keyword("Kubernetes", "kubernetes", "skill", 2),
        Keyword("Redis", "redis", "skill", 1),
        Keyword("model serving", "model serving", "phrase", 1),
    ]
    ORIGINAL = CvDocument("Built Python services with Redis", frozenset({"experience"}))

    def test_unsupported_keywords_never_raise_the_score(self) -> None:
        allowed = supported_keys(self.KEYWORDS, self.ORIGINAL, {"python", "redis"}, TAX)
        base = score_cv(self.KEYWORDS, self.ORIGINAL, TAX, allowed).score
        rng = random.Random(7)
        stuffing = ["Kubernetes", "model serving", "Kubernetes and model serving", "K8s"]
        for _ in range(50):
            extra = " ".join(rng.sample(stuffing, k=rng.randint(1, len(stuffing))))
            stuffed = CvDocument(f"{self.ORIGINAL.text}. {extra}", self.ORIGINAL.sections)
            assert score_cv(self.KEYWORDS, stuffed, TAX, allowed).score <= base

    def test_ledger_proven_skills_do_count(self) -> None:
        allowed = supported_keys(
            self.KEYWORDS, self.ORIGINAL, {"python", "redis", "kubernetes"}, TAX
        )
        tailored = CvDocument(f"{self.ORIGINAL.text} on Kubernetes", self.ORIGINAL.sections)
        before = score_cv(self.KEYWORDS, self.ORIGINAL, TAX, allowed).score
        assert score_cv(self.KEYWORDS, tailored, TAX, allowed).score > before


class TestStyleAndRendering:
    @pytest.mark.parametrize(
        "text", ["I am passionate about data", "Leveraged Redis", "Fast — and reliable"]
    )
    def test_banned_phrases_and_dashes(self, text: str) -> None:
        assert style_problems(text)

    def test_plain_text_passes(self) -> None:
        assert style_problems("Cut query time by 3x with PostgreSQL indexes") == []

    def test_lexical_similarity_bounds(self) -> None:
        assert lexical_similarity("a b c", "a b c") == pytest.approx(1.0)
        assert lexical_similarity("alpha", "beta") == 0.0

    def test_pdf_is_text_only_and_skips_removed_bullets(self) -> None:
        blocks, _ = apply_rewrites(base_blocks(LINES), [(106, "Cut LLM cost 40% with Redis • ok")])
        pdf = render_cv(blocks, title="CV")
        assert pdf.startswith(b"%PDF")
        assert cv_document(blocks).has_tables_or_images is False

    @pytest.mark.parametrize(
        ("line", "split"),
        [
            ("AIO June - August 2026", ("AIO", "June - August 2026")),
            (
                "Machine Learning Engineer, Nimbus Labs, Lahore Jul 2023 - Present",
                ("Machine Learning Engineer, Nimbus Labs, Lahore", "Jul 2023 - Present"),
            ),
            (
                "AWS Certified Cloud Practitioner, Amazon Web Services, 2024",
                ("AWS Certified Cloud Practitioner, Amazon Web Services", "2024"),
            ),
            ("ResumeRadar: NLP ranking", ("ResumeRadar: NLP ranking", "")),
            ("GPT-4 evaluation in 2024 for 3 teams", ("GPT-4 evaluation in 2024 for 3 teams", "")),
        ],
    )
    def test_dates_split_off_the_end_of_an_entry_line(
        self, line: str, split: tuple[str, str]
    ) -> None:
        assert split_dates(line) == split

    @pytest.mark.parametrize("template", CV_TEMPLATES)
    def test_every_template_passes_its_format_checks(self, template: CvTemplate) -> None:
        blocks = base_blocks(LINES)
        text, pages = read_back(render_cv(blocks, title="CV", template=template))
        assert [c.name for c in format_checks(text, pages, blocks) if not c.passed] == []

    def test_checks_catch_a_missing_line_and_too_many_pages(self) -> None:
        blocks = base_blocks(LINES)
        text, _ = read_back(render_cv(blocks, title="CV"))
        bullet = next(b for b in blocks if b.kind == "bullet")
        failed = {
            c.name
            for c in format_checks(text.replace(bullet.text.split()[0], ""), 3, blocks)
            if not c.passed
        }
        assert {"readable", "pages"} <= failed

    def test_a_long_cv_moves_to_a_template_that_fits(self) -> None:
        base = base_blocks(LINES)
        bullet = next(b for b in base if b.kind == "bullet")
        long = [*base, *(replace(bullet, text=f"{bullet.text} item {i}") for i in range(70))]
        cv = checked_cv(long, title="CV", preferred="classic")
        pages = read_back(cv.pdf)[1]
        assert pages <= MAX_CV_PAGES or not passes(cv.checks)
        assert cv.template == "compact" or passes(cv.checks)


class TestProseClaims:
    def test_the_job_applied_for_is_not_a_claim(self) -> None:
        text = "I am applying for the Senior Android Engineer role at M365connect."
        found = code_claims(text, TAX, {})
        assert {c.text for c in found} >= {"Android", "M365connect"}  # no trailing full stop
        kept = outside(found, text, ["Senior Android Engineer", "M365connect"])
        assert kept == []

    def test_at_company_stops_before_a_pronoun(self) -> None:
        text = "As an intern at Acme Analytics I automated ETL pipelines"
        assert [c.text for c in code_claims(text, TAX, {}) if c.type == "employer"] == [
            "Acme Analytics"
        ]

    def test_clause_around_a_claim(self) -> None:
        text = (
            "I cut cost by 40% with Redis caching and prompt compression, showing a focus on cost."
        )
        start = text.index("prompt")
        clause = clause_around(text, start, start + len("prompt compression"))
        assert clause == "I cut cost by 40% with Redis caching and prompt compression"
