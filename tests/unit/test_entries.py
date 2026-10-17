"""Facts belong to entries: a degree to its school, a role to its employer. Claims about the pair
are checked as a pair."""

import pytest

from app.domain.claims import FoundClaim, Ledger, LedgerRow, code_claims, merge, pair_up, route
from app.domain.ledger import Fact, accept_proposed_facts, extract_facts_by_code, reconcile
from app.domain.normalize import find_schools, is_acronym_of
from app.domain.resume import split_resume
from app.domain.taxonomy import Taxonomy, default_taxonomy
from app.services.verifier import pair_of

EDUCATION = """EDUCATION
National University of Sciences and Technology (NUST), Islamabad
BS Data Science | Sep 2023 - Present
CGPA 3.6/4.0
Roots College, Lahore
FSc Pre-Engineering, 2021 - 2023
"""

EXPERIENCE = """EXPERIENCE
Software Engineer
Nimbus Labs, Jan 2024 - Present
• Built APIs serving 1200 users
Software Engineer
Acme Analytics, 2022 - 2023
• Cut report time by 40%
"""


@pytest.fixture(scope="module")
def tax() -> Taxonomy:
    return default_taxonomy()


def by_type(facts: list[Fact], kind: str) -> dict[str, str | None]:
    return {f.normalized_value: f.context for f in facts if f.type == kind}


class TestFindSchools:
    @pytest.mark.parametrize(
        ("text", "schools"),
        [
            (
                "National University of Sciences and Technology (NUST), Islamabad",
                ["National University of Sciences and Technology (NUST)"],
            ),
            (
                "BS Data Science, Lahore University of Management Sciences",
                ["Lahore University of Management Sciences"],
            ),
            ("Pursuing BS at Lahore University", ["Lahore University"]),
            ("BS Computer Science Roots College", ["Roots College"]),
            (
                "School of Electrical Engineering and Computer Science",
                ["School of Electrical Engineering and Computer Science"],
            ),
            ("Built a school timetable app", []),
            ("BS Data Science, NUST", []),  # no institution word: left to the LLM, checked by code
        ],
    )
    def test_names_and_acronyms(self, text: str, schools: list[str]) -> None:
        assert [s.text for s in find_schools(text)] == schools

    @pytest.mark.parametrize(
        ("short", "name", "same"),
        [
            ("NUST", "National University of Sciences and Technology", True),
            ("LUMS", "Lahore University of Management Sciences", True),
            ("NUST", "Lahore University of Management Sciences", False),
            ("AI", "Acme Industries", True),
            ("Acme", "Acme Industries", False),  # a shorter name is not an acronym
        ],
    )
    def test_acronyms(self, short: str, name: str, same: bool) -> None:
        assert is_acronym_of(short, name) is same


class TestOneSchoolByAnyName:
    def test_an_acronym_joins_its_school(self, tax: Taxonomy) -> None:
        full = "National University of Sciences and Technology (NUST)"
        facts = [
            Fact("NUST", "school", "nust", 2, "entry"),
            Fact(full, "school", "national university of sciences and technology", 2, "entry"),
        ]
        assert [f.claim_text for f in reconcile(facts, [], tax)] == [full]

    def test_the_ledger_proves_both_names(self, tax: Taxonomy) -> None:
        full = "National University of Sciences and Technology (NUST)"
        ledger = Ledger.build([LedgerRow("school", "x", full, "e")])
        assert ledger.has("school", "nust") and ledger.has(
            "school", "national university of sciences and technology"
        )
        assert set(ledger.names["school"]) == {
            "National University of Sciences and Technology",
            "NUST",
        }

    def test_pages_do_not_turn_every_line_into_a_title(self, tax: Taxonomy) -> None:
        page = split_resume("ACHIEVEMENTS\nPython\nLet's Connect\nCERTIFICATIONS\nEmail\n")
        assert extract_facts_by_code(page, tax, titled_sections=False) == [
            f for f in extract_facts_by_code(page, tax) if f.type == "skill"
        ]


@pytest.fixture(scope="module")
def facts(tax: Taxonomy) -> list[Fact]:
    return extract_facts_by_code(split_resume(EDUCATION), tax)


class TestEducationEntries:
    def test_degree_school_and_gpa_share_their_entry(self, facts: list[Fact]) -> None:
        schools = by_type(facts, "school")
        entry = schools["national university of sciences and technology"]
        assert entry is not None and entry.startswith("National University of Sciences")
        assert by_type(facts, "degree")["bachelor|data science"] == entry
        gpa = next(f for f in facts if f.type == "metric" and "gpa" in f.normalized_value)
        assert gpa.context == entry

    def test_each_school_is_its_own_entry(self, facts: list[Fact]) -> None:
        schools = by_type(facts, "school")
        assert schools["roots college"] != schools["national university of sciences and technology"]
        degrees = {f.normalized_value: f.context for f in facts if f.type == "degree"}
        assert schools["roots college"] in degrees.values()

    def test_school_first_or_degree_first_both_link(self, tax: Taxonomy) -> None:
        text = "EDUCATION\nBS Data Science, 2023 - 2027\nNational University of Sciences and Technology\n"
        facts = extract_facts_by_code(split_resume(text), tax)
        assert len({f.context for f in facts if f.type in ("degree", "school")}) == 1

    def test_one_school_two_degrees_keeps_both_links(self, tax: Taxonomy) -> None:
        text = (
            "EDUCATION\nMS Data Science, Lahore University of Management Sciences, 2025 - 2027\n"
            "BS Computer Science, Lahore University of Management Sciences, 2021 - 2025\n"
        )
        facts = extract_facts_by_code(split_resume(text), tax)
        schools = [f for f in facts if f.type == "school"]
        assert len(schools) == 2 and len({f.context for f in schools}) == 2

    def test_schools_outside_education_are_not_attended(self, tax: Taxonomy) -> None:
        lines = split_resume(
            "CERTIFICATIONS\nA Crash Course in Data Science, Johns Hopkins University\n"
        )
        facts = extract_facts_by_code(lines, tax)
        assert not [f for f in facts if f.type == "school"]
        kept, rejected = accept_proposed_facts(
            [("school", "Johns Hopkins University", 2)], {ln.line_no: ln for ln in lines}, tax
        )
        assert (kept, rejected) == ([], 1)

    def test_llm_proposed_school_joins_the_entry(self, tax: Taxonomy) -> None:
        lines = split_resume("EDUCATION\nBS Data Science, NUST, 2023 - 2027\n")
        kept, _ = accept_proposed_facts(
            [("school", "NUST", 2)], {ln.line_no: ln for ln in lines}, tax
        )
        degree = next(f for f in extract_facts_by_code(lines, tax) if f.type == "degree")
        assert kept[0].context == degree.context is not None


class TestWorkEntries:
    def test_same_title_at_two_companies_is_two_facts(self, tax: Taxonomy) -> None:
        lines = split_resume(EXPERIENCE)
        by_no = {ln.line_no: ln for ln in lines}
        proposals = [
            ("role", "Software Engineer", 2),
            ("employer", "Nimbus Labs", 3),
            ("role", "Software Engineer", 5),
            ("employer", "Acme Analytics", 6),
        ]
        kept, rejected = accept_proposed_facts(proposals, by_no, tax)  # type: ignore[arg-type]
        assert rejected == 0
        facts = reconcile(kept, [], tax)
        roles = [f for f in facts if f.type == "role"]
        assert len(roles) == 2
        employers = {f.normalized_value: f.context for f in facts if f.type == "employer"}
        assert {r.context for r in roles} == set(employers.values())
        assert employers["nimbus labs"] != employers["acme analytics"]


def ledger_for(*rows: LedgerRow) -> Ledger:
    return Ledger.build(rows)


NUST = "NUST · BS Data Science"
LUMS = "LUMS · MS Computer Science"
LEDGER = ledger_for(
    LedgerRow("degree", "bachelor|data science", "BS Data Science", NUST),
    LedgerRow("school", "nust", "NUST", NUST),
    LedgerRow("degree", "master|computer science", "MS Computer Science", LUMS),
    LedgerRow("school", "lums", "LUMS", LUMS),
    LedgerRow("role", "ml engineer", "ML Engineer", "ML Engineer · Nimbus Labs"),
    LedgerRow("employer", "nimbus labs", "Nimbus Labs", "ML Engineer · Nimbus Labs"),
    LedgerRow("role", "intern", "Intern", "Intern · Acme"),
    LedgerRow("employer", "acme", "Acme", "Intern · Acme"),
)


def routed(text: str, tax: Taxonomy) -> dict[str, str]:
    """Bucket per claim text, with pairs applied as the Verifier applies them. No source line, so
    anything the ledger can't prove is Needs you."""
    claims = merge([*code_claims(text, tax, LEDGER.names), *_roles(text)])
    partners = pair_up([(c.type, c.start, c.end) for c in claims], text)
    out = {}
    for i, claim in enumerate(claims):
        partner = claims[partners[i]] if i in partners else None
        r = route(
            claim,
            ledger=LEDGER,
            taxonomy=tax,
            source_line=None,
            similarity=None,
            threshold=0.85,
            paired_with=pair_of(partner.type, partner.text, tax) if partner else None,
        )
        out[claim.text] = r.bucket
    return out


def _roles(text: str) -> list[FoundClaim]:
    """Roles come from the LLM pass in the app; tests quote them directly."""
    roles = []
    for title in ("ML Engineer", "Intern"):
        at = text.find(title)
        if at >= 0:
            roles.append(FoundClaim(title, "role", at, at + len(title), "llm"))
    return roles


class TestPairs:
    @pytest.mark.parametrize(
        ("text", "org", "bucket"),
        [
            ("BS Data Science at NUST", "NUST", "verified"),
            ("MS Computer Science at NUST", "NUST", "needs_you"),  # the MS was earned at LUMS
            ("Graduated from LUMS with an MS Computer Science", "LUMS", "verified"),
            ("ML Engineer at Nimbus Labs", "Nimbus Labs", "verified"),
            ("ML Engineer at Acme", "Acme", "needs_you"),  # was an intern there
            ("ML Engineer at Nimbus Labs, then Intern at Acme", "Acme", "verified"),
            ("Studied at NUST.", "NUST", "verified"),  # no role or degree: the school alone
        ],
    )
    def test_organisation_is_checked_with_its_role_or_degree(
        self, tax: Taxonomy, text: str, org: str, bucket: str
    ) -> None:
        assert routed(text, tax)[org] == bucket

    def test_a_role_the_ledger_lacks_is_flagged_once(self, tax: Taxonomy) -> None:
        text = "Principal Architect at Acme"
        claims = merge(
            [
                FoundClaim("Principal Architect", "role", 0, 19, "llm"),
                *code_claims(text, tax, LEDGER.names),
            ]
        )
        assert [(c.type, c.text) for c in claims] == [
            ("role", "Principal Architect"),
            ("employer", "Acme"),
        ]
        assert pair_up([(c.type, c.start, c.end) for c in claims], text) == {1: 0}
        # The role is flagged on its own; Acme, which the ledger proves, is not flagged too.
        acme = route(
            claims[1],
            ledger=LEDGER,
            taxonomy=tax,
            source_line=None,
            similarity=None,
            threshold=0.85,
            paired_with=("role", "principal architect"),
        )
        assert acme.bucket == "verified"

    def test_pairs_stay_inside_their_sentence(self) -> None:
        text = "ML Engineer for two years. Joined Acme later"
        items = [("role", 0, 11), ("employer", 33, 37)]
        assert pair_up(items, text) == {}
