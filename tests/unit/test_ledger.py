import pytest

from app.domain.ledger import (
    Fact,
    accept_proposed_facts,
    extract_facts_by_code,
    is_supported_by_line,
    normalize_fact,
)
from app.domain.resume import split_resume
from app.domain.taxonomy import Taxonomy, default_taxonomy

RESUME = """AYESHA KHAN
ayesha.khan@example.com | +92 300 1234567 | linkedin.com/in/ayesha-khan-dev | github.com/ayeshakdev

SUMMARY
Backend engineer who ships Python services.

EXPERIENCE
Software Engineer, Acme Analytics (Pvt.) Ltd., 2022 - 2024
• Reduced API latency by 30% by adding Redis caching to FastAPI services
• Built ETL pipelines in Apache Airflow processing 2M records daily

EDUCATION
BS Computer Science, NUST, 2018 - 2022 | CGPA 3.6/4.0

SKILLS
Python, SQL, Docker, PostgreSQL, React
"""


@pytest.fixture(scope="module")
def tax() -> Taxonomy:
    return default_taxonomy()


@pytest.fixture(scope="module")
def lines():  # type: ignore[no-untyped-def]
    return split_resume(RESUME)


def _line_no(lines, needle: str) -> int:  # type: ignore[no-untyped-def]
    return next(line.line_no for line in lines if needle in line.text)


class TestCodeExtraction:
    def test_extracts_skills_degrees_and_metrics_with_their_lines(self, lines, tax) -> None:  # type: ignore[no-untyped-def]
        facts = extract_facts_by_code(lines, tax)
        by_key = {(f.type, f.normalized_value): f for f in facts}

        assert ("skill", "python") in by_key
        assert ("skill", "redis") in by_key
        assert ("skill", "apache airflow") in by_key
        assert ("degree", "bachelor|computer science") in by_key
        assert ("metric", "30%") in by_key
        assert ("metric", "2000000 record") in by_key
        assert ("metric", "3.6 gpa/4") in by_key

        latency = by_key[("metric", "30%")]
        assert latency.line_no == _line_no(lines, "Reduced API latency")
        assert "latency" in latency.claim_text.lower()

    def test_dates_are_not_metrics(self, lines, tax) -> None:  # type: ignore[no-untyped-def]
        facts = extract_facts_by_code(lines, tax)
        assert not any("2022" in f.normalized_value for f in facts if f.type == "metric")

    def test_each_fact_appears_once(self, lines, tax) -> None:  # type: ignore[no-untyped-def]
        facts = extract_facts_by_code(lines, tax)
        keys = [(f.type, f.normalized_value) for f in facts]
        assert len(keys) == len(set(keys))


class TestProposedFacts:
    def test_keeps_facts_the_cited_line_supports(self, lines, tax) -> None:  # type: ignore[no-untyped-def]
        exp = _line_no(lines, "Software Engineer, Acme")
        kept, rejected = accept_proposed_facts(
            [("employer", "Acme Analytics", exp), ("role", "Software Engineer", exp)],
            {line.line_no: line for line in lines},
            tax,
        )
        assert rejected == 0
        assert [f.normalized_value for f in kept] == ["acme analytics", "software engineer"]

    @pytest.mark.parametrize(
        ("fact_type", "claim", "needle"),
        [
            ("metric", "Reduced API latency by 35%", "Reduced API latency"),  # inflated number
            ("employer", "Google", "Software Engineer, Acme"),  # employer not on the line
            ("degree", "PhD in Computer Science", "BS Computer Science"),  # fake degree level
            ("degree", "BS Data Science", "BS Computer Science"),  # wrong field
            ("skill", "Kubernetes", "Python, SQL"),  # unheld skill
            ("role", "Senior Staff Engineer", "Software Engineer, Acme"),  # inflated title
        ],
    )
    def test_rejects_fabrications(self, lines, tax, fact_type, claim, needle) -> None:  # type: ignore[no-untyped-def]
        kept, rejected = accept_proposed_facts(
            [(fact_type, claim, _line_no(lines, needle))],
            {line.line_no: line for line in lines},
            tax,
        )
        assert kept == []
        assert rejected == 1

    def test_rejects_citations_to_missing_lines(self, lines, tax) -> None:  # type: ignore[no-untyped-def]
        kept, rejected = accept_proposed_facts(
            [("skill", "Python", 999)], {ln.line_no: ln for ln in lines}, tax
        )  # noqa: E741
        assert (kept, rejected) == ([], 1)


class TestNormalizeAndSupport:
    def test_skill_synonyms_normalize_to_one_value(self, tax: Taxonomy) -> None:
        assert (
            normalize_fact("skill", "JS", tax)
            == normalize_fact("skill", "JavaScript", tax)
            == "javascript"
        )

    def test_degree_without_field_is_supported_by_a_line_with_field(self, tax: Taxonomy) -> None:
        assert is_supported_by_line("degree", "Bachelor's degree", "BS Computer Science, NUST", tax)

    def test_number_in_any_claim_must_be_on_the_line(self, tax: Taxonomy) -> None:
        assert not is_supported_by_line("project", "Led a team of 12", "Led a team of 4", tax)

    def test_fact_is_immutable_value(self) -> None:
        assert Fact("Python", "skill", "python", 1) == Fact("Python", "skill", "python", 1)
