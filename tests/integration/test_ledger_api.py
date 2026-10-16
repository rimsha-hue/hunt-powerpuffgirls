import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.api.ledger import SAMPLE_RESUME
from app.integrations.llm import LLMUnavailableError
from tests.conftest import FakeLLM, signup


def _line_id(client: TestClient, needle: str) -> int:
    lines = client.get("/api/resume/lines").json()
    return next(line["line_no"] for line in lines if needle in line["text"])


class TestResumeIngestion:
    def test_sample_resume_produces_sourced_facts(self, client: TestClient, csrf: str) -> None:
        response = client.post("/api/resume/sample", headers={"X-CSRF-Token": csrf})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["resume_version"] == 1
        assert body["llm_used"] is False  # FakeLLM() with no payload is "not configured"
        assert "deterministic" in body["llm_note"]

        facts = {(f["type"], f["normalized_value"]): f for f in body["facts"]}
        assert ("skill", "python") in facts
        assert ("degree", "bachelor|computer science") in facts
        assert ("metric", "40%") in facts

        latency = facts[("metric", "40%")]
        assert latency["source"] == "resume"
        assert latency["source_label"] == "Resume"
        assert "Reduced LLM inference cost by 40%" in latency["source_detail"]
        assert all(f["source_detail"] for f in body["facts"]), "every fact names its resume line"

        assert client.get("/api/auth/me").json()["onboarded"] is True

    def test_upload_endpoint_accepts_docx(self, client: TestClient, csrf: str) -> None:
        response = client.post(
            "/api/resume",
            files={"file": ("cv.docx", SAMPLE_RESUME.read_bytes(), "application/octet-stream")},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200, response.text
        assert len(response.json()["facts"]) >= 30

    @pytest.mark.parametrize(
        ("name", "data"),
        [
            ("cv.pdf", b"<script>alert(1)</script>"),
            ("cv.docx", b"PK\x03\x04garbage"),
            ("cv.pdf", b""),
        ],
    )
    def test_rejects_files_that_are_not_resumes(
        self, client: TestClient, csrf: str, name: str, data: bytes
    ) -> None:
        response = client.post(
            "/api/resume",
            files={"file": (name, data, "application/pdf")},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 400

    def test_rejects_oversized_uploads(self, client: TestClient, csrf: str) -> None:
        big = b"%PDF-1.7\n" + b"0" * (5 * 1024 * 1024)
        response = client.post(
            "/api/resume",
            files={"file": ("cv.pdf", big, "application/pdf")},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 400
        assert "5 MB" in response.json()["detail"]

    def test_reupload_replaces_resume_facts_but_keeps_other_sources(
        self, client: TestClient, csrf: str
    ) -> None:
        headers = {"X-CSRF-Token": csrf}
        client.post("/api/resume/sample", headers=headers)
        first = client.get("/api/ledger").json()
        second = client.post("/api/resume/sample", headers=headers).json()
        assert second["resume_version"] == 2
        assert len(client.get("/api/ledger").json()) == len(first)


class TestLlmProposals:
    def test_llm_facts_are_kept_only_when_the_cited_line_supports_them(
        self, client: TestClient, csrf: str, fake_llm: FakeLLM
    ) -> None:
        headers = {"X-CSRF-Token": csrf}
        client.post("/api/resume/sample", headers=headers)  # learn the line numbers
        employer_line = _line_id(client, "Nimbus Labs")
        latency_line = _line_id(client, "Reduced LLM inference cost")

        fake_llm.payload = {
            "facts": [
                {"claim_text": "Nimbus Labs", "type": "employer", "line_id": employer_line},
                {
                    "claim_text": "Machine Learning Engineer",
                    "type": "role",
                    "line_id": employer_line,
                },
                # Planted fabrications: an inflated number, a fake employer, a missing line.
                {
                    "claim_text": "Reduced LLM inference cost by 60%",
                    "type": "metric",
                    "line_id": latency_line,
                },
                {"claim_text": "Google", "type": "employer", "line_id": employer_line},
                {"claim_text": "Kubernetes", "type": "skill", "line_id": 999},
            ]
        }
        body = client.post("/api/resume/sample", headers=headers).json()
        assert body["llm_used"] is True
        assert body["rejected_by_code"] == 3
        values = {(f["type"], f["normalized_value"]) for f in body["facts"]}
        assert ("employer", "nimbus labs") in values
        assert ("role", "machine learning engineer") in values
        assert ("employer", "google") not in values
        assert ("metric", "60%") not in values

    def test_resume_reaches_the_llm_only_inside_a_data_block(
        self, client: TestClient, csrf: str, fake_llm: FakeLLM
    ) -> None:
        fake_llm.payload = {"facts": []}
        client.post("/api/resume/sample", headers={"X-CSRF-Token": csrf})
        (call,) = fake_llm.calls
        assert set(call) == {"resume"}
        assert "[L1]" in call["resume"]

    def test_llm_outage_falls_back_to_code(
        self, client: TestClient, csrf: str, fake_llm: FakeLLM
    ) -> None:
        fake_llm.error = LLMUnavailableError("All AI providers are unavailable right now.")
        body = client.post("/api/resume/sample", headers={"X-CSRF-Token": csrf}).json()
        assert body["llm_used"] is False
        assert "skipped" in body["llm_note"]
        assert len(body["facts"]) >= 30


class TestLedger:
    def test_search_filter_and_delete(self, client: TestClient, csrf: str) -> None:
        headers = {"X-CSRF-Token": csrf}
        client.post("/api/resume/sample", headers=headers)

        assert [
            f["normalized_value"] for f in client.get("/api/ledger", params={"q": "pytorch"}).json()
        ] == ["pytorch"]
        degrees = client.get("/api/ledger", params={"type": "degree"}).json()
        assert {f["type"] for f in degrees} == {"degree"}

        fact_id = degrees[0]["id"]
        assert client.delete(f"/api/ledger/{fact_id}", headers=headers).status_code == 204
        assert client.get("/api/ledger", params={"type": "degree"}).json() == []
        assert client.delete(f"/api/ledger/{fact_id}", headers=headers).status_code == 404

    def test_users_cannot_touch_each_others_facts(self, client: TestClient, csrf: str) -> None:
        client.post("/api/resume/sample", headers={"X-CSRF-Token": csrf})
        victim_fact = client.get("/api/ledger").json()[0]["id"]

        client.cookies.clear()
        attacker_csrf = signup(client, email="attacker@example.com")
        assert client.get("/api/ledger").json() == []
        response = client.delete(
            f"/api/ledger/{victim_fact}", headers={"X-CSRF-Token": attacker_csrf}
        )
        assert response.status_code == 404


GITHUB_REPOS = [
    {
        "name": "rag-chatbot",
        "html_url": "https://github.com/octocat/rag-chatbot",
        "description": "RAG over docs",
        "language": "Python",
        "topics": ["langchain", "hacktoberfest"],
        "fork": False,
        "pushed_at": "2026-09-01T00:00:00Z",
        "created_at": "2026-01-01T00:00:00Z",
    },
    {
        "name": "forked-thing",
        "html_url": "https://github.com/octocat/forked-thing",
        "description": "",
        "language": "Rust",
        "topics": [],
        "fork": True,
        "pushed_at": None,
        "created_at": None,
    },
]


class TestGitHubSync:
    @respx.mock
    def test_languages_and_topics_become_github_evidence(
        self, client: TestClient, csrf: str
    ) -> None:
        respx.get("https://api.github.com/users/octocat/repos").mock(
            return_value=httpx.Response(200, json=GITHUB_REPOS)
        )
        respx.get("https://api.github.com/repos/octocat/rag-chatbot/languages").mock(
            return_value=httpx.Response(200, json={"Python": 9000, "TypeScript": 1200})
        )
        headers = {"X-CSRF-Token": csrf}
        client.put("/api/profile", json={"github_username": "octocat"}, headers=headers)

        response = client.post("/api/github/sync", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["repos_scanned"] == 1  # forks are ignored
        values = {(f["type"], f["normalized_value"]) for f in body["facts"]}
        assert {("skill", "python"), ("skill", "typescript"), ("skill", "langchain")} <= values
        assert ("skill", "rust") not in values  # only seen in a fork
        assert not any("hacktoberfest" in v for _, v in values)
        assert all(
            f["source_url"] == "https://github.com/octocat/rag-chatbot" for f in body["facts"]
        )

        # Syncing again adds nothing new.
        assert client.post("/api/github/sync", headers=headers).json()["facts"] == []

    @respx.mock
    def test_unknown_user_gives_a_clear_error(self, client: TestClient, csrf: str) -> None:
        respx.get("https://api.github.com/users/nobody-here/repos").mock(
            return_value=httpx.Response(404)
        )
        headers = {"X-CSRF-Token": csrf}
        client.put("/api/profile", json={"github_username": "nobody-here"}, headers=headers)
        response = client.post("/api/github/sync", headers=headers)
        assert response.status_code == 502
        assert "not found" in response.json()["detail"]

    @respx.mock
    def test_retries_transient_failures(self, client: TestClient, csrf: str) -> None:
        route = respx.get("https://api.github.com/users/octocat/repos").mock(
            side_effect=[httpx.Response(503), httpx.Response(200, json=[])]
        )
        headers = {"X-CSRF-Token": csrf}
        client.put("/api/profile", json={"github_username": "octocat"}, headers=headers)
        assert client.post("/api/github/sync", headers=headers).status_code == 200
        assert route.call_count == 2

    def test_requires_a_username(self, client: TestClient, csrf: str) -> None:
        assert client.post("/api/github/sync", headers={"X-CSRF-Token": csrf}).status_code == 400
