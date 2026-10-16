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


PROJECTS = """PROJECTS
MediScan: AI Medicine Scanner for Pakistani Patients [link]
• Integrated Google Gemini 2.0 Flash with a pharmacist persona; 94% JSON parse success rate, 100% persona compliance, 20 screens on a zero-cost stack.
• Cross-platform Flutter app achieving 88% medicine identification accuracy
EXPERIENCE
AI Intern
AIO June – August 2026
• Led 5 engineers across 3 codebases
CERTIFICATIONS
AWS Certified Cloud Practitioner, Amazon Web Services, 2024
"""


class TestEntryAwareExtraction:
    @pytest.fixture(scope="class")
    def facts(self, tax):  # type: ignore[no-untyped-def]
        return extract_facts_by_code(split_resume(PROJECTS), tax)

    def test_one_metric_per_clause_with_its_entry(self, facts) -> None:  # type: ignore[no-untyped-def]
        metrics = {f.normalized_value: f for f in facts if f.type == "metric"}
        assert metrics["94%"].claim_text == "94% JSON parse success rate"
        assert metrics["100%"].claim_text == "100% persona compliance"
        assert metrics["20 screen"].claim_text == "20 screens on a zero-cost stack"
        mediscan = "MediScan: AI Medicine Scanner for Pakistani Patients"
        assert {metrics[k].context for k in ("94%", "100%", "20 screen", "88%")} == {mediscan}

    def test_experience_context_joins_role_and_company_without_dates(self, facts) -> None:  # type: ignore[no-untyped-def]
        led = next(f for f in facts if f.type == "metric" and "engineer" in f.normalized_value)
        assert led.normalized_value == "3 codebase|5 engineer"
        assert led.context == "AI Intern · AIO"

    def test_version_numbers_are_not_metrics(self, facts) -> None:  # type: ignore[no-untyped-def]
        assert not any(
            "flash" in f.normalized_value or "2 " in f.normalized_value
            for f in facts
            if f.type == "metric"
        )

    def test_numbers_inside_names_are_not_metrics(self, tax) -> None:  # type: ignore[no-untyped-def]
        lines = split_resume(
            "PROJECTS\n• Swin Transformer + mBART-50 + LLM post-correction for 3 scripts\n"
        )
        metrics = [
            f.normalized_value for f in extract_facts_by_code(lines, tax) if f.type == "metric"
        ]
        assert metrics == ["3 script"]

    def test_certifications_section_lines_are_certifications(self, facts) -> None:  # type: ignore[no-untyped-def]
        certs = [f.claim_text for f in facts if f.type == "certification"]
        assert certs == ["AWS Certified Cloud Practitioner, Amazon Web Services"]

    def test_label_lines_do_not_replace_the_entry_title(self, tax) -> None:  # type: ignore[no-untyped-def]
        lines = split_resume(
            "PROJECTS\nTALASH Recruitment System\nKey Contributions:\n• Scored 341 test cases\n"
        )
        (metric,) = [f for f in extract_facts_by_code(lines, tax) if f.type == "metric"]
        assert metric.context == "TALASH Recruitment System"


class TestDegreeEvidence:
    """A degree mentioned outside Education is not evidence that the person holds it."""

    def test_code_ignores_degrees_outside_education(self, tax) -> None:  # type: ignore[no-untyped-def]
        lines = split_resume(
            "SUMMARY\nI plan to pursue a PhD in Physics.\nEDUCATION\nBS Data Science, NUST\n"
        )
        degrees = [
            f.normalized_value for f in extract_facts_by_code(lines, tax) if f.type == "degree"
        ]
        assert degrees == ["bachelor|data science"]

    @pytest.mark.parametrize(
        ("line", "held"),
        [
            ("BS Data Science, NUST, 2019 - 2023", True),
            ("Pursuing BS Data Science (Sep 2023 - Present)", True),
            ("Later I plan to pursue a PhD in Robotics", False),
            ("Applying for an MS in Artificial Intelligence next year", False),
            ("I hope to do a Master's in Statistics", False),
        ],
    )
    def test_future_degrees_in_education_are_not_held(self, tax, line: str, held: bool) -> None:  # type: ignore[no-untyped-def]
        lines = split_resume(f"EDUCATION\n{line}\n")
        degrees = [f for f in extract_facts_by_code(lines, tax) if f.type == "degree"]
        assert bool(degrees) is held

    def test_llm_cannot_launder_a_mentioned_phd(self, tax) -> None:  # type: ignore[no-untyped-def]
        lines = split_resume("SUMMARY\nI plan to pursue a PhD in Physics.\n")
        kept, rejected = accept_proposed_facts(
            [("degree", "PhD in Physics", 2)], {ln.line_no: ln for ln in lines}, tax
        )
        assert (kept, rejected) == ([], 1)


class TestCodeExtraction:
    def test_extracts_skills_degrees_and_metrics_with_their_lines(self, lines, tax) -> None:  # type: ignore[no-untyped-def]
        facts = extract_facts_by_code(lines, tax)
        by_key = {(f.type, f.normalized_value): f for f in facts}

        assert ("skill", "python") in by_key
        assert ("skill", "redis") in by_key
        assert ("skill", "apache airflow") in by_key
        assert ("degree", "bachelor|computer science") in by_key
        assert ("metric", "30%") in by_key
        assert ("metric", "2000000 record/day") in by_key
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
