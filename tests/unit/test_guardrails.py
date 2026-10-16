"""Free-text guardrails: locations, role titles, contact details and the profile as a whole."""

import pytest

from app.domain.contact import (
    ContactRejectedError,
    normalize_linkedin,
    normalize_person_name,
    normalize_phone,
)
from app.domain.profile import ProfileInvalidError, ProfileRules, normalize_changes
from app.domain.roles import RoleRejectedError, normalize_role, suggest_roles
from tests.conftest import LOCATIONS

RULES = ProfileRules(LOCATIONS, phone_region="PK")


class TestLocations:
    @pytest.mark.parametrize(
        ("text", "key"),
        [
            ("Islamabad", "city:1176615"),  # own name beats a bigger city's alternate name
            ("islamabad", "city:1176615"),
            ("ISB", "city:1176615"),
            ("  Lahore ,  Pakistan ", "city:1172451"),
            ("London", "city:2643743"),  # the larger London when unqualified
            ("London, Canada", "city:6058560"),
            ("london, CA", "city:6058560"),
            ("Chittagong", "city:1205733"),
            ("USA", "country:US"),
            ("pakistan", "country:PK"),
            ("wfh", "remote"),
            ("Work from home", "remote"),
            ("city:1176615", "city:1176615"),  # already a key
        ],
    )
    def test_resolves_what_people_type(self, text: str, key: str) -> None:
        place = LOCATIONS.resolve(text)
        assert place is not None and place.key == key

    @pytest.mark.parametrize("text", ["bla bla bla", "asdf", "", "   ", "<script>", "Mars"])
    def test_rejects_non_places(self, text: str) -> None:
        assert LOCATIONS.resolve(text) is None

    def test_suggestions_rank_own_names_first(self) -> None:
        labels = [p.label for p in LOCATIONS.suggest("isl")]
        assert labels[0] == "Islamabad, Pakistan"
        assert LOCATIONS.suggest("i") == []  # too short to be useful

    def test_currencies_come_from_country_data(self) -> None:
        assert {"PKR", "USD", "GBP"} <= LOCATIONS.currencies


class TestRoles:
    @pytest.mark.parametrize(
        "title",
        [
            "Data Analyst",
            "ML Engineer",
            "Senior Software Engineers",
            "Bioinformatics Scientist",
            "AI Intern",
            "C++ Developer",
            "Head of Data",
            "Scrum Master",
        ],
    )
    def test_accepts_real_titles(self, title: str) -> None:
        assert normalize_role(f"  {title}  ") == title

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("bla bla bla", "job title"),
            ("Remote", "job title"),
            ("x", "2 to 80"),
            ("engineer engineer engineer", "job title"),
            ("Engineer at https://evil.example", "links"),
            ("Developer <script>", "punctuation"),
            ("one two three four five six seven eight nine ten eleven engineer", "10 words"),
        ],
    )
    def test_rejects_noise(self, text: str, message: str) -> None:
        with pytest.raises(RoleRejectedError, match=message):
            normalize_role(text)

    def test_suggestions(self) -> None:
        assert suggest_roles("data an")[0] == "Data Analyst"
        assert "Machine Learning Engineer" in suggest_roles("ml") or "ML Engineer" in suggest_roles(
            "ml"
        )


class TestContact:
    @pytest.mark.parametrize(
        ("raw", "e164"),
        [
            ("0300 1234567", "+923001234567"),
            ("+92 300 1234567", "+923001234567"),
            ("+1 415 555 2671", "+14155552671"),
        ],
    )
    def test_phone_numbers_are_stored_in_e164(self, raw: str, e164: str) -> None:
        assert normalize_phone(raw, "PK") == e164

    @pytest.mark.parametrize("raw", ["call me", "123", "+92 12"])
    def test_invalid_phone_numbers(self, raw: str) -> None:
        with pytest.raises(ContactRejectedError):
            normalize_phone(raw, "PK")

    def test_linkedin_is_canonical(self) -> None:
        assert (
            normalize_linkedin("linkedin.com/in/tamkeen-sara/")
            == "https://www.linkedin.com/in/tamkeen-sara"
        )
        with pytest.raises(ContactRejectedError):
            normalize_linkedin("https://evil.example/in/someone")

    def test_names(self) -> None:
        assert normalize_person_name("  Anne-Marie   O'Brien ") == "Anne-Marie O'Brien"
        assert normalize_person_name("تمکین سارہ") == "تمکین سارہ"
        with pytest.raises(ContactRejectedError):
            normalize_person_name("<b>Admin</b>")


class TestProfileChanges:
    def test_normalises_every_field(self) -> None:
        values = normalize_changes(
            {
                "target_roles": ["Data Analyst", "data analyst", "ML Engineer"],
                "target_locations": ["Remote", "isb", "Islamabad", "USA"],
                "phone": "0300 1234567",
                "salary": {"amount": 250000, "currency": "pkr", "period": "month"},
            },
            RULES,
        )
        assert values["target_roles"] == ["Data Analyst", "ML Engineer"]
        assert values["target_locations"] == ["remote", "city:1176615", "country:US"]
        assert values["phone"] == "+923001234567"
        assert values["salary_currency"] == "PKR"

    def test_reports_every_bad_field_at_once(self) -> None:
        with pytest.raises(ProfileInvalidError) as caught:
            normalize_changes(
                {
                    "target_roles": ["bla bla bla"],
                    "target_locations": ["Remote", "bla bla", "Narnia"],
                    "salary": {"amount": 1, "currency": "XYZ", "period": "month"},
                    "portfolio_url": "http://localhost:3000",
                },
                RULES,
            )
        errors = caught.value.errors
        assert set(errors) == {"target_roles", "target_locations", "salary", "portfolio_url"}
        assert (
            "'bla bla'" in errors["target_locations"] and "'Narnia'" in errors["target_locations"]
        )

    def test_null_clears_a_field(self) -> None:
        assert normalize_changes({"phone": None, "location": None}, RULES) == {
            "phone": None,
            "location": None,
        }
