import pytest

from app.domain.claims import FoundClaim, from_context
from app.domain.questions import (
    ProfileView,
    Question,
    classify,
    clean_question,
    decline_option,
    pick_option,
    profile_answer,
    render_analytical,
    saved_answer_key,
    word_limit,
)
from app.domain.research import CompanyFact, facts_from_text, own_site, select_facts
from app.integrations.forms import greenhouse_ids, parse_form_page, parse_greenhouse, parse_pasted


def q(
    label: str, field_type: str = "text", description: str = "", options: tuple[str, ...] = ()
) -> Question:
    question = clean_question(label, field_type, description=description, options=options)  # type: ignore[arg-type]
    assert question
    return question


class TestClassify:
    @pytest.mark.parametrize(
        ("label", "field_type", "expected"),
        [
            ("First Name", "text", ("factual_profile", "first_name")),
            ("Email", "text", ("factual_profile", "email")),
            ("LinkedIn Profile", "text", ("factual_profile", "linkedin")),
            ("Do you require visa sponsorship?", "select", ("factual_profile", "sponsorship")),
            (
                "When is the earliest you would want to start working with us?",
                "text",
                ("factual_profile", "notice_period"),
            ),
            ("Resume/CV", "file", ("file", "cv")),
            ("Cover Letter", "file", ("file", "cover_letter")),
            ("Agreement to Arbitrate", "select", ("consent", None)),
            ("AI Policy for Application", "select", ("consent", None)),
            ("Gender", "select", ("voluntary", None)),
            ("VeteranStatus", "select", ("voluntary", None)),
            ("What is your highest degree?", "text", ("factual_ledger", "degree")),
            ("GPA", "text", ("factual_ledger", "gpa")),
            ("How would you estimate weekly alerts per plant?", "textarea", ("analytical", None)),
            ("Why Anthropic?", "textarea", ("descriptive", None)),
            ("Additional Information", "textarea", ("descriptive", None)),
            ("Have you ever interviewed with us before?", "select", ("factual_profile", None)),
        ],
    )
    def test_types(self, label: str, field_type: str, expected: tuple[str, str | None]) -> None:
        assert classify(q(label, field_type)) == expected

    def test_consent_is_found_in_the_description_too(self) -> None:
        question = q(
            "Please read this", "select", description="I agree to the terms and conditions."
        )
        assert classify(question) == ("consent", None)


class TestLimitsAndOptions:
    def test_word_limits(self) -> None:
        assert word_limit(q("Why us?", "textarea")) == (60, 150, None)
        assert word_limit(q("Why us?", "textarea", "Great answers are often 200 - 400 words")) == (
            200,
            400,
            None,
        )
        assert word_limit(q("Why us? (max 100 words)", "textarea")) == (50, 100, None)
        assert word_limit(q("Why us?", "textarea", "500 characters max"))[2] == 500

    def test_pick_option(self) -> None:
        assert pick_option("no", ("Yes", "No")) == "No"
        assert pick_option("Yes", ("Yes, I am authorized", "No")) == "Yes, I am authorized"
        assert pick_option("Maybe", ("Yes", "No")) is None
        assert pick_option("anything", ()) == "anything"

    def test_decline(self) -> None:
        assert (
            decline_option(("Male", "Female", "I don't wish to answer")) == "I don't wish to answer"
        )
        assert decline_option(("Yes", "No")) is None

    def test_saved_key_ignores_case_and_punctuation(self) -> None:
        assert saved_answer_key("How did you hear about us?") == saved_answer_key(
            "how did you hear about us"
        )


class TestProfileAnswers:
    VIEW = ProfileView(
        first_name="Ayesha",
        email="a@example.com",
        work_authorization="citizen",
        notice_period="1 month",
    )

    def test_direct_fields(self) -> None:
        assert profile_answer("first_name", q("First Name"), self.VIEW) == "Ayesha"
        assert profile_answer("phone", q("Phone"), self.VIEW) is None  # not in the profile: ask

    def test_sponsorship_follows_work_authorization(self) -> None:
        question = q("Do you require visa sponsorship?", "select", options=("Yes", "No"))
        assert profile_answer("sponsorship", question, self.VIEW) == "No"
        permit = ProfileView(work_authorization="work_permit")
        assert profile_answer("sponsorship", question, permit) is None  # depends: ask

    def test_saved_answers(self) -> None:
        view = ProfileView(saved={saved_answer_key("How did you hear about us?"): "LinkedIn"})
        assert profile_answer(None, q("How did you hear about us?"), view) == "LinkedIn"

    def test_analytical_layout(self) -> None:
        text = render_analytical(
            ["Ten machines"], ["Count alerts", "Average them"], "About 40", "Seasonal load"
        )
        assert text.splitlines() == [
            "Assumptions: Ten machines.",
            "Approach:",
            "1. Count alerts",
            "2. Average them",
            "Result: About 40",
            "Risk: Seasonal load",
        ]


class TestResearch:
    TEXT = (
        "### Menu | Careers. Acme builds maintenance software used by 300 factories across Europe. "
        "You will love working here, we promise you that it is great. "
        "acme is great. Our partner network grew quickly in the last two quarters of the year. "
        "Acme opened an office in Lahore in 2026 to support customers across South Asia."
    )

    def test_facts_are_whole_sentences_about_the_company(self) -> None:
        facts = [f.fact for f in facts_from_text(self.TEXT, "Acme", "https://acme.example/about")]
        # Skipped: the markdown menu, the second-person line, the too-short line, and the line
        # that doesn't name the company.
        assert facts == [
            "Acme builds maintenance software used by 300 factories across Europe.",
            "Acme opened an office in Lahore in 2026 to support customers across South Asia.",
        ]

    def test_own_site_first(self) -> None:
        facts = [
            CompanyFact(
                "Acme raised a round of funding from several large investors.",
                "https://news.example/a",
            ),
            CompanyFact(
                "Acme builds maintenance software used by many large factories.",
                "https://www.acme.com/about",
            ),
        ]
        assert own_site("https://www.acme.com/about", "Acme")
        assert [f.source_url for f in select_facts(facts, "Acme")][
            0
        ] == "https://www.acme.com/about"


class TestFormParsing:
    def test_greenhouse_ids(self) -> None:
        assert greenhouse_ids("https://job-boards.greenhouse.io/anthropic/jobs/5186067008") == (
            "anthropic",
            "5186067008",
        )
        assert greenhouse_ids("https://boards.greenhouse.io/embed/job_app?for=acme&token=123") == (
            "acme",
            "123",
        )
        assert greenhouse_ids("https://example.com/jobs/1") is None

    def test_greenhouse_payload(self) -> None:
        data = {
            "questions": [
                {
                    "label": "First Name",
                    "required": True,
                    "fields": [{"name": "first_name", "type": "input_text", "values": []}],
                },
                {
                    "label": "Resume/CV",
                    "required": False,
                    "fields": [
                        {"name": "resume", "type": "input_file"},
                        {"name": "resume_text", "type": "textarea"},
                    ],
                },
                {
                    "label": "Why Acme?",
                    "required": True,
                    "description": "<p>Tell us <b>why</b>.</p>",
                    "fields": [{"name": "q1", "type": "textarea"}],
                },
            ],
            "location_questions": [
                {"label": "Latitude", "fields": [{"name": "latitude", "type": "input_hidden"}]}
            ],
            "compliance": [
                {
                    "questions": [
                        {
                            "label": "Gender",
                            "fields": [
                                {
                                    "name": "gender",
                                    "type": "multi_value_single_select",
                                    "values": [{"label": "Decline To Self Identify"}],
                                }
                            ],
                        }
                    ]
                }
            ],
        }
        questions = parse_greenhouse(data)
        assert [(x.label, x.field_type) for x in questions] == [
            ("First Name", "text"),
            ("Resume/CV", "file"),
            ("Why Acme?", "textarea"),
            ("Gender", "select"),
        ]
        assert questions[2].description == "Tell us why ."

    def test_generic_page(self) -> None:
        html = """
        <form>
          <label for="n">Full name *</label><input id="n" name="name" required>
          <label>Email <input type="email" name="email"></label>
          <input type="hidden" name="csrf" value="x">
          <textarea aria-label="Why do you want this job?" name="why"></textarea>
          <select name="visa"><option value="">Pick</option><option value="y">Yes</option><option value="n">No</option></select>
          <fieldset><legend>Willing to relocate?</legend>
            <label for="r1">Yes</label><input type="radio" id="r1" name="relocate" value="y">
            <label for="r2">No</label><input type="radio" id="r2" name="relocate" value="n">
          </fieldset>
          <input type="file" name="resume" aria-label="Resume">
          <script>var x = "<input name='evil'>"</script>
        </form>"""
        found = {x.label: x for x in parse_form_page(html)}
        assert set(found) == {
            "Full name",
            "Email",
            "Why do you want this job?",
            "visa",
            "Resume",
            "Willing to relocate?",
        }
        assert found["Full name"].required
        assert found["visa"].options == ("Yes", "No")
        assert found["Willing to relocate?"].options == ("Yes", "No")
        assert found["Resume"].field_type == "file"

    def test_pasted(self) -> None:
        questions = parse_pasted("Why do you want to work here? *\n\n- Portfolio link\nok\n")
        assert [(x.label, x.field_type, x.required) for x in questions] == [
            ("Why do you want to work here?", "textarea", True),
            ("Portfolio link", "text", False),
        ]


class TestContext:
    FACT = "Acme builds maintenance software used by 300 factories across Europe."

    def _claim(self, text: str, quote: str) -> FoundClaim:
        start = text.index(quote)
        return FoundClaim(quote, "metric", start, start + len(quote), "code")

    def test_restating_the_company_fact_is_not_a_claim_about_the_user(self) -> None:
        text = "Your software runs in 300 factories across Europe, which is why I applied."
        assert from_context(self._claim(text, "300 factories"), text, [self.FACT])

    def test_a_claim_about_the_user_is_kept(self) -> None:
        text = "I cut maintenance costs by 300 factories worth of downtime at my last job."
        assert not from_context(self._claim(text, "300 factories"), text, [self.FACT])
        other = "My models served 400 factories."
        assert not from_context(self._claim(other, "400 factories"), other, [self.FACT])

    def test_the_companys_product_is_not_the_users_claim(self) -> None:
        fact = "Anthropic is an AI safety company best known for developing Claude."
        text = "Anthropic's work on Claude matters to me, and I built apps with Claude."
        first = text.index("Claude")
        second = text.rindex("Claude")

        def claim(start: int) -> FoundClaim:
            return FoundClaim("Claude", "skill", start, start + 6, "code")

        assert from_context(claim(first), text, [fact], "Anthropic")
        assert not from_context(claim(second), text, [fact], "Anthropic")

    def test_a_phrase_copied_from_a_fact_is_the_companys(self) -> None:
        fact = "Anthropic is committing $150 million over three years to the Genesis Mission."
        text = "I admire a company that commits $150 million to the Genesis Mission."
        money = FoundClaim(
            "$150 million", "metric", text.index("$150"), text.index("$150") + 12, "llm"
        )
        start = text.index("Genesis")
        mission = FoundClaim("Genesis Mission", "project", start, start + 15, "llm")
        assert from_context(money, text, [fact])
        assert from_context(mission, text, [fact])

    def test_a_multi_word_skill_stays_the_users(self) -> None:
        fact = "Anthropic develops Claude, a family of large language models."
        text = "I build products with large language models every day."
        start = text.index("large")
        skill = FoundClaim("large language models", "skill", start, start + 21, "code")
        assert not from_context(skill, text, [fact], "Anthropic")

    def test_a_company_number_pinned_on_the_user_is_still_checked(self) -> None:
        fact = "Acme builds maintenance software used by 300 factories across Europe."
        text = "At Nimbus I served 300 factories with the same models."
        start = text.index("300")
        claim = FoundClaim("300 factories", "metric", start, start + 13, "llm")
        assert not from_context(claim, text, [fact], "Acme")
