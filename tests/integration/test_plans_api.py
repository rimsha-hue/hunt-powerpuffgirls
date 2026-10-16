"""Phase 6 end to end: a skill gap becomes a plan, a qualifying repo turns it into Verified evidence,
the open claim for it is resolved, and the Activity screen counts it."""

import base64
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.assets import RECORDED_JOBS
from app.config import Settings
from app.integrations.jobs.sources import RecordedSource
from app.main import create_app
from app.services.scout import ScoutSources
from tests.conftest import LOCATIONS, signup
from tests.integration.test_jobs_api import POSTINGS, FakeEmail, FakeSource, _onboard
from tests.integration.test_workspace_api import ScriptedLLM

API = "https://api.github.com/repos/ayesha-dev/k8s-demo"
REPO_URL = "https://github.com/ayesha-dev/k8s-demo"
PLAN = {
    "title": "Deploy a FastAPI service on Kubernetes",
    "why": "Running your own service on a cluster shows you can ship with Kubernetes.",
    "steps": [
        "Containerise a small FastAPI app with Docker.",
        "Write Kubernetes manifests for a deployment and a service.",
        "Run it on a local kind cluster and add a readiness probe.",
        "Document the setup in the README.",
    ],
    "hours": 10,
}
# Line 10 of the demo resume, rewritten with the skill the user can't prove yet.
WITH_GAP = "Shipped FastAPI microservices on Docker and Kubernetes with GitHub Actions CI/CD"


def _llm(plan: dict[str, Any] = PLAN) -> ScriptedLLM:
    return ScriptedLLM(
        {"PlanDraft": plan, "TailorOutput": {"lines": [{"line": 10, "text": WITH_GAP}]}}
    )


def _client(settings: Settings, llm: ScriptedLLM) -> TestClient:
    sources = ScoutSources([FakeSource("fake", POSTINGS)], [], RecordedSource(RECORDED_JOBS))
    return TestClient(
        create_app(settings, llm=llm, locations=LOCATIONS, job_sources=sources, email=FakeEmail())
    )


@pytest.fixture
def ws(settings: Settings) -> Iterator[TestClient]:
    with _client(settings, _llm()) as client:
        yield client


def _setup(client: TestClient) -> tuple[dict[str, str], int]:
    headers = _onboard(client)
    profile = client.put("/api/profile", json={"github_username": "ayesha-dev"}, headers=headers)
    assert profile.status_code == 200, profile.text
    client.post("/api/jobs/fetch", headers=headers)
    job = next(j for j in client.get("/api/jobs").json() if "Kubernetes" in j["missing_skills"])
    return headers, job["id"]


def _mock_github(commits: int) -> None:
    today = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    respx.get(API).mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "k8s-demo",
                "html_url": REPO_URL,
                "owner": {"login": "ayesha-dev"},
                "fork": False,
                "private": False,
                "created_at": today,
                "description": "FastAPI on a cluster",
                "topics": ["kubernetes"],
            },
        )
    )
    respx.get(f"{API}/languages").mock(return_value=httpx.Response(200, json={"Python": 1}))
    readme = base64.b64encode(b"Runs on Kubernetes.").decode()
    respx.get(f"{API}/readme").mock(
        return_value=httpx.Response(200, json={"encoding": "base64", "content": readme})
    )
    page = [{"commit": {"author": {"date": today}}}]
    link = f'<{API}/commits?per_page=1&page={commits}>; rel="last"' if commits > 1 else ""
    respx.get(f"{API}/commits").mock(
        return_value=httpx.Response(200, json=page, headers={"link": link})
    )


class TestPlans:
    def test_a_gap_becomes_a_plan_once(self, ws: TestClient) -> None:
        headers, job_id = _setup(ws)
        made = ws.post(
            "/api/plans", json={"skill": "kubernetes", "job_id": job_id}, headers=headers
        )
        assert made.status_code == 201, made.text
        plan = made.json()
        assert plan["skill"] == "Kubernetes" and plan["written_by"] == "llm"
        assert plan["status"] == "open" and len(plan["steps"]) == 4
        assert "github.com/ayesha-dev" in plan["acceptance"][0]
        assert plan["job_title"] and "Machine Learning Engineer" in plan["job_title"]

        again = ws.post("/api/plans", json={"skill": "K8s"}, headers=headers)
        assert again.json()["id"] == plan["id"]
        assert len(ws.get("/api/plans").json()) == 1

    def test_unknown_or_proven_skills_are_refused(self, ws: TestClient) -> None:
        headers, _ = _setup(ws)
        unknown = ws.post("/api/plans", json={"skill": "basket weaving"}, headers=headers)
        assert unknown.status_code == 422
        assert unknown.json()["detail"][0]["loc"] == ["body", "skill"]
        proven = ws.post("/api/plans", json={"skill": "Python"}, headers=headers)
        assert proven.status_code == 409 and "already proves" in proven.json()["detail"]

    def test_a_broken_draft_falls_back_to_the_template(self, settings: Settings) -> None:
        broken = {**PLAN, "steps": PLAN["steps"][:2], "why": "A passionate deep dive."}
        with _client(settings, _llm(broken)) as client:
            headers, _ = _setup(client)
            plan = client.post("/api/plans", json={"skill": "Kubernetes"}, headers=headers).json()
        assert plan["written_by"] == "template" and len(plan["steps"]) == 5

    def test_steps_move_the_plan_along(self, ws: TestClient) -> None:
        headers, _ = _setup(ws)
        plan = ws.post("/api/plans", json={"skill": "Kubernetes"}, headers=headers).json()
        done = ws.put(f"/api/plans/{plan['id']}/steps/0", json={"done": True}, headers=headers)
        assert done.json()["status"] == "in_progress" and done.json()["steps"][0]["done"]
        undone = ws.put(f"/api/plans/{plan['id']}/steps/0", json={"done": False}, headers=headers)
        assert undone.json()["status"] == "open"
        missing = ws.put(f"/api/plans/{plan['id']}/steps/9", json={"done": True}, headers=headers)
        assert missing.status_code == 404

    @respx.mock
    def test_a_qualifying_repo_turns_the_gap_into_verified(self, ws: TestClient) -> None:
        headers, job_id = _setup(ws)
        app = ws.post(f"/api/jobs/{job_id}/tailor", headers=headers).json()
        gap = next(c for c in app["claims"] if c["text"] == "Kubernetes")
        assert gap["bucket"] == "needs_you"
        plan = ws.post(
            "/api/plans", json={"skill": "Kubernetes", "job_id": job_id}, headers=headers
        ).json()

        bad_link = ws.post(
            f"/api/plans/{plan['id']}/verify",
            json={"repo_url": "https://gitlab.com/x/y"},
            headers=headers,
        )
        assert bad_link.status_code == 409

        _mock_github(commits=1)
        early = ws.post(
            f"/api/plans/{plan['id']}/verify", json={"repo_url": REPO_URL}, headers=headers
        ).json()
        assert early["verified"] is False
        assert [c["name"] for c in early["plan"]["checks"] if not c["passed"]] == ["commits"]
        assert early["plan"]["status"] == "in_progress"
        assert not any(f["normalized_value"] == "kubernetes" for f in ws.get("/api/ledger").json())

        _mock_github(commits=5)
        done = ws.post(
            f"/api/plans/{plan['id']}/verify", json={"repo_url": REPO_URL}, headers=headers
        ).json()
        assert done["verified"] is True and done["plan"]["status"] == "verified"
        fact = next(
            f for f in ws.get("/api/ledger").json() if f["normalized_value"] == "kubernetes"
        )
        assert fact["source"] == "github" and fact["source_url"] == REPO_URL

        # The open claim is now Verified, and the job no longer lists the gap.
        app = ws.get(f"/api/jobs/{job_id}/application").json()
        claim = next(c for c in app["claims"] if c["text"] == "Kubernetes")
        assert claim["bucket"] == "verified" and claim["source"]["label"] == "GitHub"
        assert "Kubernetes" not in ws.get(f"/api/jobs/{job_id}").json()["missing_skills"]

        again = ws.post(
            f"/api/plans/{plan['id']}/verify", json={"repo_url": REPO_URL}, headers=headers
        )
        assert again.status_code == 409

        activity = ws.get("/api/activity").json()
        assert activity["counters"]["plans_verified"] == 1
        assert activity["counters"]["unverified_shipped"] == 0
        assert activity["counters"]["checked_automatically"] > 0
        actions = [e["action"] for e in activity["events"]]
        assert "plan_verified" in actions and "skill_proven_by_repo" in actions
        assert activity["questions"][0]["questions"] >= 1

    def test_plans_are_private(self, ws: TestClient) -> None:
        headers, _ = _setup(ws)
        plan = ws.post("/api/plans", json={"skill": "Kubernetes"}, headers=headers).json()
        ws.post("/api/auth/logout", headers=headers)
        other = {"X-CSRF-Token": signup(ws, "other@example.com")}
        assert ws.get("/api/plans").json() == []
        hidden = ws.put(f"/api/plans/{plan['id']}/steps/0", json={"done": True}, headers=other)
        assert hidden.status_code == 404
        assert ws.delete(f"/api/plans/{plan['id']}", headers=other).status_code == 404
