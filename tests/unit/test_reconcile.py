"""Same fact in different words is one fact; course issuers are not employers; new fact types."""

import pytest

from app.domain.ledger import Fact, accept_proposed_facts, extract_facts_by_code, reconcile
from app.domain.resume import heading_section, split_resume
from app.domain.taxonomy import Taxonomy, default_taxonomy


@pytest.fixture(scope="module")
def tax() -> Taxonomy:
    return default_taxonomy()


def values(facts: list[Fact]) -> list[str]:
    return [f.normalized_value for f in facts]


class TestSameFactDifferentWords:
    @pytest.mark.parametrize(
        ("kind", "existing", "new"),
        [
            ("role", "Director, ML & Deep Learning", "Director of ML/DL"),
            ("role", "Director, ML & Deep Learning", "Director, Machine Learning & Deep Learning"),
            ("employer", "NUST Society of AI", "NUST Society of Artificial Intelligence"),
            (
                "certification",
                "Machine Learning Specialization",
                "Machine Learning Specialization, Andrew Ng",
            ),
            (
                "certification",
                "A Crash Course in Data Science",
                "Data Science Crash Course, Johns Hopkins",
            ),
            (
                "certification",
                "Quantum AI Technology Workshop",
                "The Quantum 2050 AI Technology Workshop",
            ),
            ("project", "NAZAR", "nazar core"),
        ],
    )
    def test_rewordings_join_the_existing_fact(
        self, tax: Taxonomy, kind: str, existing: str, new: str
    ) -> None:
        merged = reconcile(
            [Fact(new, kind, new.lower(), 1)], [(kind, "existing-value", existing)], tax
        )  # type: ignore[arg-type]
        assert values(merged) == ["existing-value"]

    @pytest.mark.parametrize(
        ("kind", "existing", "new"),
        [
            ("role", "AI Intern", "AI & Quantum Computing Intern"),  # a skill tells them apart
            ("role", "Software Engineer", "Senior Software Engineer"),  # so does a level
            ("employer", "Google", "Google Cloud"),
            ("certification", "Python Crash Course", "Networking Basics Course"),
        ],
    )
    def test_different_facts_stay_apart(
        self, tax: Taxonomy, kind: str, existing: str, new: str
    ) -> None:
        kept = reconcile(
            [Fact(new, kind, "new-value", 1)], [(kind, "existing-value", existing)], tax
        )  # type: ignore[arg-type]
        assert values(kept) == ["new-value"]

    def test_metric_with_fewer_numbers_folds_into_the_fuller_one(self, tax: Taxonomy) -> None:
        facts = [
            Fact("88% medicine identification accuracy", "metric", "88%", 3),
            Fact("88% accuracy across 25 real pharmacy labels", "metric", "25 label|88%", 3),
        ]
        assert values(reconcile(facts, [], tax)) == ["25 label|88%"]

    def test_duplicates_inside_one_document_are_dropped(self, tax: Taxonomy) -> None:
        facts = [
            Fact("Director of ML/DL", "role", "a", 1),
            Fact("Director, ML & DL", "role", "b", 2),
        ]
        assert values(reconcile(facts, [], tax)) == ["a"]


class TestWhereEmployersComeFrom:
    @pytest.mark.parametrize(
        ("document", "employer"),
        [
            (
                "CERTIFICATIONS\nA Crash Course in Data Science, Johns Hopkins University",
                "Johns Hopkins University",
            ),
            (
                "EXPERIENCE\nCompleted Networking Basics course, Cisco Networking Academy",
                "Cisco Networking Academy",
            ),
            (
                "EDUCATION\nBS Data Science, National University of Sciences and Technology",
                "National University of Sciences and Technology",
            ),
        ],
    )
    def test_issuers_and_schools_are_not_employers(
        self, tax: Taxonomy, document: str, employer: str
    ) -> None:
        lines = split_resume(document)
        kept, rejected = accept_proposed_facts(
            [("employer", employer, lines[-1].line_no)], {ln.line_no: ln for ln in lines}, tax
        )
        assert (kept, rejected) == ([], 1)

    def test_real_employers_still_count(self, tax: Taxonomy) -> None:
        lines = split_resume("EXPERIENCE\nAI Intern\nAIO June - August 2026")
        kept, _ = accept_proposed_facts(
            [("employer", "AIO", 3), ("role", "AI Intern", 2)],
            {ln.line_no: ln for ln in lines},
            tax,
        )
        assert [f.normalized_value for f in kept] == ["aio", "ai intern"]

    def test_bare_numbers_are_not_metrics(self, tax: Taxonomy) -> None:
        lines = split_resume("PROJECTS\n• K-Means (k=4) across 77 community areas")
        kept, rejected = accept_proposed_facts(
            [("metric", "k=4", 2)], {ln.line_no: ln for ln in lines}, tax
        )
        assert (kept, rejected) == ([], 1)


class TestNewTypesAndHeadings:
    def test_publications_and_awards_come_from_their_sections(self, tax: Taxonomy) -> None:
        lines = split_resume(
            "PUBLICATIONS\nNetwork Transformative Learning (LAK 2026) [link]\n"
            "• Co-authored paper published by Springer\n"
            "HONORS & AWARDS\nDean's Honor List, 2024\n"
        )
        facts = {(f.type, f.claim_text) for f in extract_facts_by_code(lines, tax)}
        assert ("publication", "Network Transformative Learning (LAK 2026)") in facts
        assert ("award", "Dean's Honor List") in facts
        assert not any(t == "publication" and "Co-authored" in c for t, c in facts)

    @pytest.mark.parametrize(
        ("heading", "section"),
        [
            ("Research Experience", "publications"),
            ("VOLUNTEER EXPERIENCE", "activities"),
            ("Honors & Awards", "awards"),
            ("Licenses & Certifications", "certifications"),
            ("Professional Experience:", "experience"),
            ("Technical Skills", "skills"),
        ],
    )
    def test_compound_headings_are_recognised(self, heading: str, section: str) -> None:
        assert heading_section(heading) == section

    @pytest.mark.parametrize(
        "line",
        [
            "Built a research pipeline for 3 labs.",
            "Led the volunteer team of 20 people",
            "AI Intern",
        ],
    )
    def test_ordinary_lines_are_not_headings(self, line: str) -> None:
        assert heading_section(line) is None
