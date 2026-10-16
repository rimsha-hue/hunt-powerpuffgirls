"""Flow B end to end on SQLite: a planted fabrication is caught, the user's answers are applied,
the CV is approved and rendered, and a confirmed claim is Verified on the next application."""

from collections.abc import Iterator, Mapping
from typing import Any, TypeVar

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.assets import RECORDED_JOBS
from app.config import Settings
from app.integrations.jobs.sources import RecordedSource
from app.main import create_app
from app.services.scout import ScoutSources
from tests.conftest import LOCATIONS, signup
from tests.integration.test_jobs_api import POSTINGS, FakeEmail, FakeSource, _onboard

T = TypeVar("T", bound=BaseModel)

# Line 9 of the demo resume: "Reduced LLM inference cost by 40% by adding Redis response caching
# and prompt compression". The rewrite inflates the number and adds a skill the user never showed.
FABRICATED = "Cut LLM inference cost by 45% with Redis caching on Kubernetes"


class ScriptedLLM:
    """Answers each schema with its scripted payload (an empty one by default)."""

    configured = True

    def __init__(self, payloads: Mapping[str, dict[str, Any]]) -> None:
        self.payloads = dict(payloads)
        self.calls: list[str] = []

    async def generate(
        self, *, schema: type[T], system: str, task: str, data: Mapping[str, str]
    ) -> T:
        self.calls.append(schema.__name__)
        return schema.model_validate(self.payloads.get(schema.__name__, {}))


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM({"TailorOutput": {"lines": [{"line": 9, "text": FABRICATED}]}})


@pytest.fixture
def ws(settings: Settings, llm: ScriptedLLM) -> Iterator[TestClient]:
    sources = ScoutSources([FakeSource("fake", POSTINGS)], [], RecordedSource(RECORDED_JOBS))
    app = create_app(settings, llm=llm, locations=LOCATIONS, job_sources=sources, email=FakeEmail())
    with TestClient(app) as client:
        yield client


def _setup(client: TestClient) -> tuple[dict[str, str], list[int]]:
    headers = _onboard(client)
    client.post("/api/jobs/fetch", headers=headers)
    return headers, [j["id"] for j in client.get("/api/jobs").json()]


def _claim(app: dict[str, Any], text: str) -> dict[str, Any]:
    return next(c for c in app["claims"] if c["text"] == text)


class TestWorkspace:
    def test_fabrication_caught_answered_approved_and_learned(self, ws: TestClient) -> None:
        headers, (job_id, second_job) = _setup(ws)

        tailored = ws.post(f"/api/jobs/{job_id}/tailor", headers=headers)
        assert tailored.status_code == 200, tailored.text
        app = tailored.json()
        assert _claim(app, "45%")["bucket"] == "needs_you"
        assert _claim(app, "Kubernetes")["bucket"] == "needs_you"
        redis = _claim(app, "Redis")
        assert redis["bucket"] == "verified" and redis["source"]["label"] == "Resume"
        assert app["questions_asked"] == app["needs_you"] >= 2
        assert ws.get(f"/api/jobs/{job_id}").json()["status"] == "in_review"
        assert any(b["rewritten"] and b["text"] == FABRICATED for b in app["blocks"])

        blocked = ws.post(f"/api/jobs/{job_id}/application/approve", headers=headers)
        assert blocked.status_code == 409 and "need you" in blocked.json()["detail"]
        assert ws.get(f"/api/jobs/{job_id}/application/cv.pdf").status_code == 409

        no_proof = ws.post(
            f"/api/claims/{_claim(app, 'Kubernetes')['id']}/decision",
            json={"action": "confirm"},
            headers=headers,
        )
        assert no_proof.status_code == 409
        app = ws.post(
            f"/api/claims/{_claim(app, 'Kubernetes')['id']}/decision",
            json={"action": "confirm", "url": "https://github.com/hero/k8s-operator"},
            headers=headers,
        ).json()
        assert _claim(app, "Kubernetes")["bucket"] == "verified"
        assert _claim(app, "Kubernetes")["source"]["label"] == "Confirmed by you"

        app = ws.post(
            f"/api/claims/{_claim(app, '45%')['id']}/decision",
            json={"action": "false"},
            headers=headers,
        ).json()
        assert _claim(app, "45%")["bucket"] == "removed"
        assert not any("45%" in b["text"] for b in app["blocks"])

        for claim in [c for c in app["claims"] if c["bucket"] == "needs_you"]:
            app = ws.post(
                f"/api/claims/{claim['id']}/decision", json={"action": "remove"}, headers=headers
            ).json()
        approved = ws.post(f"/api/jobs/{job_id}/application/approve", headers=headers)
        assert approved.status_code == 200 and approved.json()["status"] == "cv_approved"
        pdf = ws.get(f"/api/jobs/{job_id}/application/cv.pdf")
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        assert ws.post(f"/api/jobs/{job_id}/tailor", headers=headers).status_code == 409

        # Learning loop: the confirmed skill is Verified and the false number is blocked.
        again = ws.post(f"/api/jobs/{second_job}/tailor", headers=headers).json()
        assert _claim(again, "Kubernetes")["bucket"] == "verified"
        assert any(c["bucket"] == "blocked" and c["text"] == "45%" for c in again["claims"])
        assert not any("45%" in b["text"] for b in again["blocks"])
        assert again["questions_asked"] < app["questions_asked"] + 2

    def test_edit_rewords_and_routes_again(self, ws: TestClient) -> None:
        headers, (job_id, _) = _setup(ws)
        app = ws.post(f"/api/jobs/{job_id}/tailor", headers=headers).json()
        edited = ws.post(
            f"/api/claims/{_claim(app, '45%')['id']}/decision",
            json={"action": "edit", "text": "40%"},
            headers=headers,
        ).json()
        assert _claim(edited, "40%")["bucket"] == "verified"
        assert any("by 40% with Redis" in b["text"] for b in edited["blocks"])
        kubernetes = _claim(edited, "Kubernetes")
        block = edited["blocks"][kubernetes["block_index"]]["text"]
        assert block[kubernetes["span_start"] : kubernetes["span_end"]] == "Kubernetes"

    def test_ats_after_ignores_the_unproven_skill(self, ws: TestClient) -> None:
        headers, (job_id, _) = _setup(ws)
        app = ws.post(f"/api/jobs/{job_id}/tailor", headers=headers).json()
        assert "Kubernetes" not in app["ats"]["matched"]
        assert app["ats"]["after"] >= 0 and app["ats"]["checks"]["no_tables_or_images"] is True

    def test_without_an_llm_bullets_keep_the_users_words(self, settings: Settings) -> None:
        sources = ScoutSources([FakeSource("fake", POSTINGS)], [], RecordedSource(RECORDED_JOBS))
        app = create_app(
            settings,
            llm=ScriptedLLM({}),
            locations=LOCATIONS,
            job_sources=sources,
            email=FakeEmail(),
        )
        app.state.llm.configured = False
        with TestClient(app) as client:
            headers, (job_id, _) = _setup(client)
            body = client.post(f"/api/jobs/{job_id}/tailor", headers=headers).json()
        assert not any(b["rewritten"] for b in body["blocks"])
        assert body["needs_you"] == 0
        assert "reordered by relevance" in body["note"]

    def test_claims_are_private(self, ws: TestClient) -> None:
        headers, (job_id, _) = _setup(ws)
        app = ws.post(f"/api/jobs/{job_id}/tailor", headers=headers).json()
        claim_id = _claim(app, "45%")["id"]
        ws.cookies.clear()
        other = {"X-CSRF-Token": signup(ws, "villain@example.com")}
        response = ws.post(
            f"/api/claims/{claim_id}/decision", json={"action": "remove"}, headers=other
        )
        assert response.status_code == 404
        assert ws.get(f"/api/jobs/{job_id}/application").status_code == 404
