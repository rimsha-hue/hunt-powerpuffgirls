"""Flow D end to end on SQLite: Greenhouse questions, typed answers from the profile and ledger,
a descriptive answer citing one sourced company fact, an analytical answer whose invented number
needs the user, consent left to the user, approvals, the Apply Pack, and "I submitted it"."""

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.assets import RECORDED_JOBS
from app.config import Settings
from app.integrations.fetch import FetchError
from app.integrations.jobs.sources import RecordedSource
from app.main import create_app
from app.services.scout import ScoutSources
from tests.conftest import LOCATIONS
from tests.integration.test_jobs_api import FakeEmail, FakeSource, _onboard, _posting
from tests.integration.test_workspace_api import ScriptedLLM

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/123"
TAVILY = "https://api.tavily.com/search"
FACT = "Acme builds maintenance software used by 300 factories across Europe."


def _field(name: str, kind: str, values: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"name": name, "type": kind, "values": [{"label": v, "value": v} for v in values]}


QUESTIONS = {
    "title": "Machine Learning Engineer",
    "company_name": "Acme",
    "location": {"name": "Remote"},
    "absolute_url": "https://acme.example/careers?gh_jid=123",
    "content": "&lt;p&gt;We need &lt;b&gt;Python&lt;/b&gt; and PyTorch.&lt;/p&gt;",
    "first_published": "2026-10-10T00:00:00Z",
    "questions": [
        {"label": "First Name", "required": True, "fields": [_field("first_name", "input_text")]},
        {"label": "Email", "required": True, "fields": [_field("email", "input_text")]},
        {"label": "Resume/CV", "required": True, "fields": [_field("resume", "input_file")]},
        {"label": "Why Acme?", "required": True, "fields": [_field("q1", "textarea")]},
        {
            "label": "Do you require visa sponsorship?",
            "required": True,
            "fields": [_field("q2", "multi_value_single_select", ("Yes", "No"))],
        },
        {
            "label": "How would you estimate the maintenance alerts a plant receives each week?",
            "required": True,
            "fields": [_field("q3", "textarea")],
        },
        {
            "label": "Agreement to Arbitrate",
            "required": True,
            "fields": [_field("q4", "multi_value_single_select", ("I agree to the terms.",))],
        },
        {
            "label": "How did you hear about us?",
            "required": False,
            "fields": [_field("q5", "input_text")],
        },
    ],
    "compliance": [
        {
            "questions": [
                {
                    "label": "Gender",
                    "required": False,
                    "fields": [
                        _field(
                            "gender",
                            "multi_value_single_select",
                            ("Female", "Male", "Decline To Self Identify"),
                        )
                    ],
                }
            ]
        }
    ],
}
WHY = (
    "Your software runs in 300 factories across Europe, and reliable systems at that scale are what I "
    "like building. At Nimbus Labs I reduced LLM inference cost by 40% by adding Redis response caching "
    "and prompt compression. I also shipped FastAPI microservices on Docker and AWS ECS with GitHub "
    "Actions CI/CD. I would bring the same care for cost and reliability to your maintenance models, "
    "where a small saving repeats across every plant you serve."
)
ANALYTICAL = {
    "assumptions": ["A plant runs 200 machines", "Each machine raises alerts independently"],
    "steps": ["Count alerts per machine from history", "Multiply by the number of machines"],
    "result": "Roughly the per-machine rate times the fleet size.",
    "risk": "Seasonal load changes the rate.",
}


class UnreachableFetcher:
    """Every page fetch fails, as when an apply page needs a login or JavaScript."""

    async def fetch(self, url: str, accept: tuple[str, ...] = ()) -> Any:
        raise FetchError("Could not connect to the site.")


@pytest.fixture
def routes() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(GREENHOUSE, name="greenhouse").mock(
            return_value=httpx.Response(200, json=QUESTIONS)
        )
        mock.post(TAVILY, name="tavily").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {"url": "https://acme.example/about", "content": f"### Menu. {FACT}"}
                    ]
                },
            )
        )
        yield mock


@pytest.fixture
def client(settings: Settings, routes: respx.MockRouter) -> Iterator[TestClient]:
    settings = settings.model_copy(update={"tavily_api_key": SecretStr("test-key")})
    postings = [
        _posting(
            "g1",
            "Machine Learning Engineer",
            company="Acme",
            apply_url="https://job-boards.greenhouse.io/acme/jobs/123",
        ),
        _posting(
            "g2",
            "Machine Learning Engineer II",
            company="Acme",
            apply_url="https://job-boards.greenhouse.io/acme/jobs/123",
        ),
    ]
    sources = ScoutSources([FakeSource("fake", postings)], [], RecordedSource(RECORDED_JOBS))
    llm = ScriptedLLM(
        {"DescriptiveAnswer": {"answer": WHY, "fact": 1}, "AnalyticalAnswer": ANALYTICAL}
    )
    app = create_app(
        settings,
        llm=llm,
        locations=LOCATIONS,
        job_sources=sources,
        email=FakeEmail(),
        fetcher=UnreachableFetcher(),  # type: ignore[arg-type]
    )
    with TestClient(app) as test_client:
        yield test_client


def _ready_cv(client: TestClient, headers: dict[str, str], job_id: int) -> None:
    app = client.post(f"/api/jobs/{job_id}/tailor", headers=headers).json()
    for claim in [c for c in app["claims"] if c["bucket"] == "needs_you"]:
        client.post(
            f"/api/claims/{claim['id']}/decision", json={"action": "remove"}, headers=headers
        )
    assert (
        client.post(f"/api/jobs/{job_id}/application/approve", headers=headers).status_code == 200
    )


def _setup(client: TestClient) -> tuple[dict[str, str], list[int]]:
    headers = _onboard(client)
    client.put("/api/profile", json={"work_authorization": "citizen"}, headers=headers)
    client.post("/api/jobs/fetch", headers=headers)
    job_ids = sorted(j["id"] for j in client.get("/api/jobs").json())
    for job_id in job_ids:
        _ready_cv(client, headers, job_id)
    return headers, job_ids


def _by_label(app: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {a["label"]: a for a in app["form"]["answers"]}


class TestFormApply:
    def test_greenhouse_form_end_to_end(self, client: TestClient) -> None:
        headers, (job_id, second_job) = _setup(client)
        base = f"/api/jobs/{job_id}/application"

        found = client.post(f"{base}/questions/discover", headers=headers)
        assert found.status_code == 200, found.text
        form = found.json()["form"]
        assert form["source"] == "greenhouse"
        types = {a["label"]: a["type"] for a in form["answers"]}
        assert types["Why Acme?"] == "descriptive"
        assert types["Agreement to Arbitrate"] == "consent"
        assert types["Gender"] == "voluntary"

        app = client.post(f"{base}/answers/draft", headers=headers).json()
        answers = _by_label(app)
        assert (
            answers["First Name"]["answer"] == "Hero" and answers["First Name"]["status"] == "ready"
        )
        assert answers["Email"]["answer"] == "hero@example.com"
        assert answers["Resume/CV"]["status"] == "ready"
        assert answers["Do you require visa sponsorship?"]["answer"] == "No"
        assert answers["Gender"]["answer"] == "Decline To Self Identify"
        assert answers["Agreement to Arbitrate"]["status"] == "needs_you"
        assert answers["Agreement to Arbitrate"]["answer"] == ""
        assert answers["How did you hear about us?"]["status"] == "needs_you"

        why = answers["Why Acme?"]
        assert why["company_fact"] == {"fact": FACT, "url": "https://acme.example/about"}
        assert why["status"] == "ready", why
        why_claims = [c for c in app["claims"] if c["document"] == f"q:{why['index']}"]
        assert {c["text"]: c["bucket"] for c in why_claims}["40%"] == "verified"
        assert not any(c["text"].startswith("300") for c in why_claims)  # the company's fact

        estimate = answers[
            "How would you estimate the maintenance alerts a plant receives each week?"
        ]
        assert estimate["answer"].startswith("Assumptions: A plant runs 200 machines.")
        invented = [
            c
            for c in app["claims"]
            if c["document"] == f"q:{estimate['index']}" and "200" in c["text"]
        ]
        assert [c["bucket"] for c in invented] == ["needs_you"]
        assert estimate["status"] == "needs_you"

        early = client.post(f"{base}/submitted", headers=headers)
        assert early.status_code == 409

        app = client.post(
            f"/api/claims/{invented[0]['id']}/decision", json={"action": "remove"}, headers=headers
        ).json()
        assert _by_label(app)[estimate["label"]]["status"] == "ready"
        assert "200" not in _by_label(app)[estimate["label"]]["answer"]

        consent = answers["Agreement to Arbitrate"]
        wrong = client.put(
            f"{base}/answers/{consent['index']}", json={"answer": "Maybe"}, headers=headers
        )
        assert wrong.status_code == 409
        client.put(
            f"{base}/answers/{consent['index']}",
            json={"answer": "I agree to the terms.", "remember": True},
            headers=headers,
        )
        hear = answers["How did you hear about us?"]
        client.put(
            f"{base}/answers/{hear['index']}",
            json={"answer": "A friend", "remember": True},
            headers=headers,
        )

        app = client.get(base).json()
        for answer in app["form"]["answers"]:
            approved = client.post(f"{base}/answers/{answer['index']}/approve", headers=headers)
            assert approved.status_code == 200, (answer["label"], approved.text)
        app = client.get(base).json()
        assert app["form"]["blockers"] == []

        done = client.post(f"{base}/submitted", headers=headers)
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "submitted"
        assert done.json()["form"]["follow_up_due"] is not None
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "applied"
        assert (
            client.put(f"{base}/answers/0", json={"answer": "x"}, headers=headers).status_code
            == 409
        )

        # The next form remembers the saved answer, never the legal agreement.
        second = f"/api/jobs/{second_job}/application"
        client.post(f"{second}/questions/discover", headers=headers)
        again = _by_label(client.post(f"{second}/answers/draft", headers=headers).json())
        assert again["How did you hear about us?"]["answer"] == "A friend"
        assert again["How did you hear about us?"]["source"]["label"] == "Saved answer"
        assert again["Agreement to Arbitrate"]["answer"] == ""

    def test_unreadable_form_falls_back_to_pasting(
        self, client: TestClient, routes: respx.MockRouter
    ) -> None:
        headers, (job_id, _) = _setup(client)
        base = f"/api/jobs/{job_id}/application"
        routes["greenhouse"].return_value = httpx.Response(404)
        failed = client.post(f"{base}/questions/discover", headers=headers)
        assert failed.status_code == 409 and "Paste" in failed.json()["detail"]
        pasted = client.post(
            f"{base}/questions/paste",
            json={"text": "Why do you want to join Acme? *\nPortfolio link"},
            headers=headers,
        ).json()
        assert [a["type"] for a in pasted["form"]["answers"]] == ["descriptive", "factual_profile"]
        assert pasted["form"]["source"] == "pasted"

    def test_needs_an_approved_cv(self, client: TestClient) -> None:
        headers = _onboard(client)
        client.post("/api/jobs/fetch", headers=headers)
        job_id = client.get("/api/jobs").json()[0]["id"]
        client.post(f"/api/jobs/{job_id}/tailor", headers=headers)
        response = client.post(
            f"/api/jobs/{job_id}/application/questions/discover", headers=headers
        )
        assert response.status_code == 409 and "Approve your CV" in response.json()["detail"]


class TestAddByLink:
    def test_import_a_greenhouse_job(self, client: TestClient) -> None:
        headers = _onboard(client)
        url = "https://job-boards.greenhouse.io/acme/jobs/123"
        added = client.post("/api/jobs/import", json={"url": url}, headers=headers)
        assert added.status_code == 201, added.text
        job = added.json()
        assert (job["title"], job["company"], job["remote"]) == (
            "Machine Learning Engineer",
            "Acme",
            True,
        )
        assert job["apply_url"] == url  # the board page carries the form
        assert job["url"] == "https://acme.example/careers?gh_jid=123"
        assert "Python" in client.get(f"/api/jobs/{job['id']}").json()["description"]
        again = client.post("/api/jobs/import", json={"url": url}, headers=headers)
        assert again.json()["id"] == job["id"]  # no duplicate

    def test_other_links_are_refused(self, client: TestClient) -> None:
        headers = _onboard(client)
        response = client.post(
            "/api/jobs/import", json={"url": "https://example.com/job/1"}, headers=headers
        )
        assert response.status_code == 400 and "Greenhouse" in response.json()["detail"]
