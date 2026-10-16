from datetime import UTC, datetime

import pytest

from app.domain.ats import (
    CvDocument,
    Keyword,
    cv_from_lines,
    extract_keywords,
    score_cv,
    weighted_sections,
)
from app.domain.classify import classify, find_apply_email, required_attachments
from app.domain.jobs import (
    JobPosting,
    clean_line,
    dedupe_hash,
    location_fit,
    title_relevance,
)
from app.domain.locations import REMOTE, Place
from app.domain.matching import match_job
from app.domain.status import InvalidTransitionError, transition
from app.domain.taxonomy import default_taxonomy
from app.integrations.jobs.base import JobQuery
from app.services.scout import build_queries

TAX = default_taxonomy()
LAHORE = Place("city:1172451", "city", "Lahore", "PK", "Pakistan")
PAKISTAN = Place("country:PK", "country", "Pakistan", "PK", "Pakistan")
LONDON_CA = Place("city:6058560", "city", "London", "CA", "Canada")


def _posting(**overrides: object) -> JobPosting:
    values: dict[str, object] = {
        "source": "jsearch",
        "external_id": "1",
        "title": "Machine Learning Engineer",
        "company": "Acme",
        "location": "Lahore, PK",
        "remote": False,
        "url": "https://example.com/job/1",
        "description": "",
        "posted_at": datetime(2026, 10, 1, tzinfo=UTC),
        "country_code": "PK",
    }
    values.update(overrides)
    return JobPosting(**values)  # type: ignore[arg-type]


class TestStatus:
    def test_happy_path_to_an_outcome(self) -> None:
        status = "new"
        for target in ("shortlisted", "in_review", "approved", "applied", "interview", "offer"):
            status = transition(status, target)
        assert status == "offer"

    @pytest.mark.parametrize(
        ("current", "target"),
        [("new", "applied"), ("new", "approved"), ("rejected", "new"), ("applied", "new")],
    )
    def test_skipping_a_gate_is_refused(self, current: str, target: str) -> None:
        with pytest.raises(InvalidTransitionError):
            transition(current, target)

    def test_skipped_jobs_can_come_back(self) -> None:
        assert transition(transition("new", "skipped"), "new") == "new"

    def test_unknown_status_is_refused(self) -> None:
        with pytest.raises(InvalidTransitionError):
            transition("new", "hired")


class TestRelevance:
    @pytest.mark.parametrize(
        ("role", "title"),
        [
            ("Machine Learning Engineer", "ML Engineer - Predictive Maintenance"),
            ("AI Engineer", "Senior AI / LLM Engineer (GenAI Systems)"),
            ("Backend Engineer", "Senior Back-End Engineer, Work From Home"),
            ("Machine Learning Engineer", "AI/ML Developer"),
        ],
    )
    def test_relevant_titles(self, role: str, title: str) -> None:
        assert title_relevance(role, title) >= 0.5

    @pytest.mark.parametrize(
        ("role", "title"),
        [
            ("Backend Engineer", "Tech Lead Full-Stack Rails Engineer"),
            ("Data Analyst", "Remote Office Assistant"),
            ("Machine Learning Engineer", "Freelance Copywriter"),
        ],
    )
    def test_irrelevant_titles(self, role: str, title: str) -> None:
        assert title_relevance(role, title) < 0.5

    def test_same_field_different_job_scores_lower(self) -> None:
        same = title_relevance("Data Engineer", "Data Engineer")
        other = title_relevance("Data Engineer", "Data Analyst")
        assert same == 1.0
        assert other < same


class TestPlaces:
    def test_city_matches_by_name_and_country(self) -> None:
        assert location_fit(_posting(), [LAHORE])
        assert not location_fit(_posting(location="London, ON", country_code="CA"), [LAHORE])

    def test_same_city_name_in_another_country_does_not_match(self) -> None:
        assert not location_fit(_posting(location="London, GB", country_code="GB"), [LONDON_CA])

    def test_country_and_remote(self) -> None:
        assert location_fit(_posting(location="Karachi, PK"), [PAKISTAN])
        assert location_fit(_posting(location="Anywhere", remote=True, country_code=None), [REMOTE])
        assert not location_fit(_posting(remote=False), [REMOTE])

    def test_no_targets_means_anywhere(self) -> None:
        assert location_fit(_posting(location="Berlin", country_code="DE"), [])


class TestDedupeAndQueries:
    def test_hash_ignores_case_spacing_and_legal_suffix(self) -> None:
        a = dedupe_hash("Acme Pvt Ltd", "ML  Engineer", "Lahore")
        b = dedupe_hash("acme", "ml engineer", "LAHORE")
        assert a == b
        assert a != dedupe_hash("Acme", "ML Engineer", "Karachi")

    def test_replacement_character_becomes_a_dash(self) -> None:
        assert clean_line("ML Engineer � Remote ") == "ML Engineer - Remote"

    def test_small_budget_still_covers_every_role_and_place(self) -> None:
        roles = ["ML Engineer", "AI Engineer", "Backend Engineer"]
        queries = build_queries(roles, [REMOTE, LAHORE, PAKISTAN], 3)
        assert {q.role for q in queries} == set(roles)
        assert {q.place for q in queries} == {REMOTE, LAHORE, PAKISTAN}

    def test_budget_never_exceeds_pairs_and_no_repeats(self) -> None:
        queries = build_queries(["ML Engineer", "AI Engineer"], [LAHORE], 10)
        assert len(queries) == len(set(queries)) == 2
        assert build_queries(["AI Engineer"], [], 3) == [JobQuery("AI Engineer", None)]


class TestClassifier:
    def test_email_in_an_apply_sentence(self) -> None:
        text = "We build robots.\nInterested candidates should send their CV and cover letter to Jobs@Acme.io."
        result = classify(text, "https://acme.io/careers/1")
        assert result.method == "email"
        assert result.apply_email == "jobs@acme.io"
        assert result.attachments == ("cv", "cover_letter")

    def test_footer_addresses_are_not_apply_addresses(self) -> None:
        text = "Questions about privacy? Email privacy@acme.io. Apply through our careers page."
        assert find_apply_email(text) is None
        assert classify(text, "https://acme.io/apply").method == "form"

    def test_unknown_without_email_or_url(self) -> None:
        assert classify("A great role.", None).method == "unknown"

    def test_attachments_only_from_apply_sentences(self) -> None:
        text = "Our API references are public. Please include your portfolio and two references."
        assert required_attachments(text) == ("cv", "portfolio", "references")
        assert required_attachments("We maintain API references.") == ("cv",)


JD = """About us
We make maintenance software.

Requirements
- 3+ years with Python and PostgreSQL
- Experience with Docker
Nice to have: Kubernetes
- Experience with Terraform

Responsibilities:
- Build services in Go
"""


class TestAts:
    def test_section_weights(self) -> None:
        weights = {line.strip(): w for w, line in weighted_sections(JD) if line.strip()}
        assert weights["- 3+ years with Python and PostgreSQL"] == 2
        assert weights["Kubernetes"] == 1  # an inline label applies to its own line
        assert weights["- Experience with Terraform"] == 2  # and the section carries on
        assert weights["- Build services in Go"] == 1

    def test_keywords_are_weighted_skills(self) -> None:
        keywords = {k.term: k.weight for k in extract_keywords(JD, TAX) if k.kind == "skill"}
        assert keywords["Python"] == 2
        assert keywords["PostgreSQL"] == 2
        assert keywords["Docker"] == 2
        assert keywords["Kubernetes"] == 1
        assert keywords["Go"] == 1

    def test_a_skill_on_its_own_line_is_not_a_heading(self) -> None:
        text = "Requirements\nSQL\nAWS\nPython"
        assert [w for w, _ in weighted_sections(text)] == [2, 2, 2]

    def test_score_formula_and_synonyms(self) -> None:
        keywords = [
            Keyword("Python", "python", "skill", 2),
            Keyword("JavaScript", "javascript", "skill", 2),
            Keyword("model serving", "model serving", "phrase", 1),
        ]
        cv = CvDocument(
            text="ayesha@example.com\nBuilt APIs in Python and JS for model serving.",
            sections=frozenset({"experience", "education", "skills"}),
        )
        result = score_cv(keywords, cv, TAX)
        assert [k.term for k in result.matched] == ["Python", "JavaScript", "model serving"]
        assert result.coverage == 1.0
        assert result.score == 100

    def test_missing_keywords_and_format_penalties(self) -> None:
        keywords = [Keyword("Python", "python", "skill", 2), Keyword("Go", "go", "skill", 2)]
        cv = cv_from_lines([("experience", "Wrote Python services")])
        result = score_cv(keywords, cv, TAX)
        assert [k.term for k in result.missing] == ["Go"]
        assert result.checks == {
            "standard_headings": False,
            "contact_info": False,
            "two_pages_max": True,
        }
        assert result.score == round(100 * (0.85 * 0.5 + 0.15 * (1 / 3)))

    def test_unknown_tables_check_is_left_out(self) -> None:
        cv = CvDocument(text="x", sections=frozenset(), has_tables_or_images=True)
        assert score_cv([], cv, TAX).checks["no_tables_or_images"] is False
        assert "no_tables_or_images" not in score_cv([], cv_from_lines([]), TAX).checks

    def test_keywords_round_trip_through_json(self) -> None:
        keyword = Keyword("Python", "python", "skill", 2)
        assert Keyword.from_json(keyword.to_json()) == keyword


class TestMatcher:
    KEYWORDS = [
        Keyword("Python", "python", "skill", 2),
        Keyword("Docker", "docker", "skill", 2),
        Keyword("Kubernetes", "kubernetes", "skill", 1),
        Keyword("model serving", "model serving", "phrase", 1),
    ]

    def test_score_from_ledger_skills_role_and_place(self) -> None:
        result = match_job(self.KEYWORDS, {"python", "kubernetes"}, 1.0, True)
        # skills 3 of 5 weight, smoothed by a prior of 2 at 50%; role 1.0; place 1.0
        assert result.score == round(100 * (0.6 * (3 + 1) / (5 + 2) + 0.3 + 0.1))
        assert result.matched_skills == ["Python", "Kubernetes"]
        assert result.missing_skills == ["Docker"]

    def test_required_gaps_come_first(self) -> None:
        result = match_job(self.KEYWORDS, set(), 0.5, False)
        assert result.missing_skills == ["Python", "Docker", "Kubernetes"]

    def test_one_matched_skill_is_not_a_perfect_match(self) -> None:
        short = match_job([Keyword("Python", "python", "skill", 1)], {"python"}, 1.0, True)
        assert short.score < 90
        long = [Keyword(f"S{i}", f"s{i}", "skill", 2) for i in range(20)]
        assert match_job(long, {f"s{i}" for i in range(20)}, 1.0, True).score >= 97

    def test_only_ledger_evidence_raises_the_score(self) -> None:
        without = match_job(self.KEYWORDS, {"python"}, 1.0, True).score
        unrelated = match_job(self.KEYWORDS, {"python", "cobol"}, 1.0, True).score
        assert unrelated == without
