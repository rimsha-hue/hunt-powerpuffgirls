import pytest

from app.domain.taxonomy import Taxonomy, default_taxonomy


@pytest.fixture(scope="module")
def tax() -> Taxonomy:
    return default_taxonomy()


class TestCanonical:
    @pytest.mark.parametrize(
        ("term", "expected"),
        [
            ("JS", "JavaScript"),
            ("javascript", "JavaScript"),
            ("k8s", "Kubernetes"),
            ("Postgres", "PostgreSQL"),
            ("sklearn", "scikit-learn"),
            ("Golang", "Go"),
            ("react", "React"),
            ("RAG", "Retrieval-Augmented Generation"),
        ],
    )
    def test_maps_synonyms_to_canonical_name(self, tax: Taxonomy, term: str, expected: str) -> None:
        assert tax.canonical(term) == expected

    def test_unknown_term_is_none(self, tax: Taxonomy) -> None:
        assert tax.canonical("underwater basket weaving") is None


class TestFind:
    def test_finds_skills_in_a_skills_line(self, tax: Taxonomy) -> None:
        line = "Python, TypeScript, React.js, Node.js, PostgreSQL, Docker, AWS (S3, Lambda)"
        assert tax.skills_in(line) >= {
            "Python",
            "TypeScript",
            "React",
            "Node.js",
            "PostgreSQL",
            "Docker",
            "AWS",
            "Amazon S3",
        }

    def test_longest_match_wins(self, tax: Taxonomy) -> None:
        assert tax.skills_in("Built services in Node.js and Ruby on Rails") == {
            "Node.js",
            "Ruby on Rails",
        }

    def test_symbols_in_names(self, tax: Taxonomy) -> None:
        assert tax.skills_in("Wrote C++ and C# services on .NET") == {"C++", "C#", ".NET"}

    @pytest.mark.parametrize(
        "text",
        [
            "Helped the rest of the team go to market faster",
            "Led our go-to-market plan",
            "Spring 2024 internship at Oracle",
            "Received grade C in Chemistry.",
            "Teams react quickly to incidents",
            "Attached my CV and cover letter",
            "We excel at shipping",
            "Plan B and plan C were rejected",
        ],
    )
    def test_ordinary_words_are_not_skills(self, tax: Taxonomy, text: str) -> None:
        assert tax.skills_in(text) == set()

    def test_single_letter_languages_need_list_context(self, tax: Taxonomy) -> None:
        assert tax.skills_in("Languages: Python, C, R, Java") >= {"Python", "C", "R", "Java"}
        assert "C" in tax.skills_in("Embedded work in C programming")
        assert "C" in tax.skills_in("C/C++")

    def test_case_sensitive_short_forms(self, tax: Taxonomy) -> None:
        assert "Machine Learning" in tax.skills_in("Applied ML to fraud detection")
        assert tax.skills_in("html email templates") == {"HTML"}
        assert "Go" in tax.skills_in("Microservices in Go and Rust")

    def test_rest_apis_only_when_meant(self, tax: Taxonomy) -> None:
        assert "REST APIs" in tax.skills_in("Designed RESTful APIs for billing")
        assert "REST APIs" not in tax.skills_in("the rest of the backlog")

    def test_spans_point_at_the_text(self, tax: Taxonomy) -> None:
        text = "Shipped FastAPI services"
        (match,) = tax.find(text)
        assert text[match.start : match.end] == "FastAPI"
