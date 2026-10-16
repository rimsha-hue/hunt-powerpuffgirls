import pytest

from app.domain.normalize import (
    content_tokens,
    degree_keys,
    find_quantities,
    normalize_org,
    quantity_keys,
)


class TestQuantities:
    @pytest.mark.parametrize(
        ("text", "key"),
        [
            ("reduced latency by 30%", "30%"),
            ("cut costs 30 percent", "30%"),
            ("improved recall by 12.5 %", "12.5%"),
            ("3x faster builds", "3x"),
            ("saved $1.2M a year", "1200000 usd"),
            ("saved $250k", "250000 usd"),
            ("served 10k users", "10000 user"),
            ("served 10,000 users", "10000 user"),
            ("5+ years of experience", "5 year"),
            ("GPA 3.8/4.0", "3.8 gpa/4"),
            ("Rs. 50,000 stipend", "50000 pkr"),
        ],
    )
    def test_parses_value_and_unit(self, text: str, key: str) -> None:
        assert key in quantity_keys(text)

    def test_different_numbers_never_match(self) -> None:
        assert quantity_keys("by 30%") != quantity_keys("by 35%")

    def test_same_number_different_unit_never_match(self) -> None:
        assert quantity_keys("30% faster") != quantity_keys("30 users")

    def test_calendar_years_are_dates_not_metrics(self) -> None:
        assert quantity_keys("Software Engineer, 2021 - 2023") == set()
        years = find_quantities("since 2019", include_years=True)
        assert [q.unit for q in years] == ["year-date"]

    def test_every_number_is_found(self) -> None:
        text = "Cut p95 latency 40% for 2 services serving 1.5M requests"
        keys = quantity_keys(text)
        assert {"40%", "2 service", "1500000 request"} <= keys


class TestDegrees:
    @pytest.mark.parametrize(
        ("text", "key"),
        [
            ("BS Computer Science, NUST", "bachelor|computer science"),
            ("BS in Data Science", "bachelor|data science"),
            ("Bachelor of Science in Computer Science", "bachelor|computer science"),
            ("BSCS from FAST", "bachelor|computer science"),
            ("B.Sc. Electrical Engineering", "bachelor|electrical engineering"),
            ("MS Artificial Intelligence", "master|artificial intelligence"),
            ("Master's in Statistics", "master|statistics"),
            ("PhD in Physics", "phd|physics"),
            ("MBA", "master"),
        ],
    )
    def test_normalizes_degrees(self, text: str, key: str) -> None:
        assert key in degree_keys(text)

    def test_spellings_of_the_same_degree_agree(self) -> None:
        assert degree_keys("BSCS") == degree_keys("BS Computer Science")

    @pytest.mark.parametrize(
        "text", ["Advanced MS Excel user", "Based in Boston, MA", "BE the change"]
    )
    def test_ambiguous_short_forms_are_ignored(self, text: str) -> None:
        assert degree_keys(text) == set()


class TestOrgsAndTokens:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Systems Ltd.", "systems"),
            ("Arbisoft (Pvt.) Ltd.", "arbisoft"),
            ("Acme, Inc.", "acme"),
            ("10Pearls LLC", "10pearls"),
            ("Google", "google"),
        ],
    )
    def test_normalize_org_strips_legal_suffixes(self, raw: str, expected: str) -> None:
        assert normalize_org(raw) == expected

    def test_content_tokens_drop_stopwords(self) -> None:
        assert content_tokens("Built the ETL pipeline for Acme") == {
            "built",
            "etl",
            "pipeline",
            "acme",
        }
