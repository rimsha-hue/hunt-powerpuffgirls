"""Flow C end to end on SQLite: draft from the approved CV, verify the email, gate on claims and
attachments, send with the CV and cover letter attached, and mark the job Applied."""

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.assets import RECORDED_JOBS
from app.config import Settings
from app.integrations.jobs.sources import RecordedSource
from app.main import create_app
from app.services.scout import ScoutSources
from tests.conftest import LOCATIONS
from tests.integration.test_jobs_api import FakeEmail, FakeSource, _onboard, _posting
from tests.integration.test_workspace_api import ScriptedLLM

JD = (
    "We build maintenance models in Python and PyTorch.\n"
    "To apply, email your CV, a cover letter and your academic transcript to jobs@acme.example.\n"
    "Please use the subject line 'ML Engineer - Your Name'."
)
# About 130 words, with one claim the evidence can't support ("a team of 12").
BODY = (
    "Dear Hiring Manager,\n\n"
    "I am applying for the Machine Learning Engineer role at OpsSense. At Nimbus Labs I reduced LLM "
    "inference cost by 40% by adding Redis response caching and prompt compression, and I shipped "
    "FastAPI microservices on Docker and AWS ECS. I also led a team of 12 engineers through a "
    "platform migration that the whole company relied on.\n\n"
    "Your work on predictive maintenance fits how I like to build: careful evaluation first, then "
    "steady delivery. I have fine-tuned a BERT model with PyTorch and cut dashboard query time by "
    "3x with PostgreSQL indexes, so I am comfortable across modelling and the systems around it.\n\n"
    "My CV and cover letter are attached, along with my transcript. Thank you for reading, and I "
    "would be glad to talk about the role whenever suits you."
)


@pytest.fixture
def mailbox() -> FakeEmail:
    return FakeEmail()


@pytest.fixture
def app_client(settings: Settings, mailbox: FakeEmail) -> Iterator[TestClient]:
    postings = [_posting("e1", "Machine Learning Engineer", description=JD, apply_url=None)]
    sources = ScoutSources([FakeSource("fake", postings)], [], RecordedSource(RECORDED_JOBS))
    llm = ScriptedLLM({"Prose": {"body": BODY}})
    app = create_app(settings, llm=llm, locations=LOCATIONS, job_sources=sources, email=mailbox)
    with TestClient(app) as client:
        yield client


def _approved_cv(client: TestClient) -> tuple[dict[str, str], int]:
    headers = _onboard(client)
    client.post("/api/jobs/fetch", headers=headers)
    [job] = client.get("/api/jobs").json()
    app = client.post(f"/api/jobs/{job['id']}/tailor", headers=headers).json()
    for claim in [c for c in app["claims"] if c["bucket"] == "needs_you"]:
        client.post(
            f"/api/claims/{claim['id']}/decision", json={"action": "remove"}, headers=headers
        )
    assert (
        client.post(f"/api/jobs/{job['id']}/application/approve", headers=headers).status_code
        == 200
    )
    return headers, job["id"]


def _claim(app: dict[str, Any], document: str, text: str) -> dict[str, Any]:
    return next(c for c in app["claims"] if c["document"] == document and text in c["text"])


class TestEmailApply:
    def test_compose_verify_gate_and_send(self, app_client: TestClient, mailbox: FakeEmail) -> None:
        headers, job_id = _approved_cv(app_client)
        mailbox.sent.clear()  # the digest from the fetch

        app = app_client.post(f"/api/jobs/{job_id}/application/compose", headers=headers).json()
        email = app["email"]
        assert email["recipient"] == "jobs@acme.example"
        assert email["subject"] == "ML Engineer - Hero"
        assert [a["kind"] for a in email["attachments"]] == ["cv", "cover_letter", "transcript"]
        assert email["attachments"][2]["status"] == "missing"
        assert app["email"]["cover_letter"], "a cover letter was asked for, so one is drafted"

        assert _claim(app, "email", "40%")["bucket"] == "verified"
        fabricated = _claim(app, "email", "12")
        assert fabricated["bucket"] == "needs_you"
        assert any("claim(s) that need you" in b for b in email["blockers"])
        assert any("Transcript" in b for b in email["blockers"])
        refused = app_client.post(f"/api/jobs/{job_id}/application/send", headers=headers)
        assert refused.status_code == 409
        assert mailbox.sent == []

        for claim in [c for c in app["claims"] if c["bucket"] == "needs_you"]:
            app = app_client.post(
                f"/api/claims/{claim['id']}/decision", json={"action": "remove"}, headers=headers
            ).json()
        assert "team of 12" not in " ".join(b["text"] for b in app["email"]["blocks"])

        pdf = b"%PDF-1.4\n% transcript\n"
        uploaded = app_client.post(
            f"/api/jobs/{job_id}/application/files/transcript",
            files={"file": ("transcript.pdf", pdf, "application/pdf")},
            headers=headers,
        ).json()
        assert uploaded["email"]["attachments"][2]["status"] == "uploaded"
        assert uploaded["email"]["blockers"] == []

        sent = app_client.post(f"/api/jobs/{job_id}/application/send", headers=headers)
        assert sent.status_code == 200, sent.text
        [message] = mailbox.sent
        assert message.to == "jobs@acme.example"
        assert message.subject == "ML Engineer - Hero"
        assert message.reply_to == "hero@example.com"
        assert "Best regards,\nHero" in message.text
        assert [a.content_type for a in message.attachments] == ["application/pdf"] * 3
        assert all(a.data.startswith(b"%PDF") for a in message.attachments)
        body = sent.json()
        assert body["status"] == "sent"
        due = datetime.fromisoformat(body["email"]["follow_up_due"])
        sent_at = datetime.fromisoformat(body["email"]["sent_at"])
        assert due - sent_at == timedelta(days=7)
        assert app_client.get(f"/api/jobs/{job_id}").json()["status"] == "applied"

        again = app_client.post(f"/api/jobs/{job_id}/application/send", headers=headers)
        assert again.status_code == 409 and len(mailbox.sent) == 1

    def test_waive_edit_and_reverify(self, app_client: TestClient) -> None:
        headers, job_id = _approved_cv(app_client)
        app_client.post(f"/api/jobs/{job_id}/application/compose", headers=headers)
        waived = app_client.post(
            f"/api/jobs/{job_id}/application/attachments/transcript/waive",
            json={"waived": True},
            headers=headers,
        ).json()
        assert waived["email"]["attachments"][2]["status"] == "waived"
        cv_waive = app_client.post(
            f"/api/jobs/{job_id}/application/attachments/cv/waive",
            json={"waived": True},
            headers=headers,
        )
        assert cv_waive.status_code == 409

        edited = app_client.put(
            f"/api/jobs/{job_id}/application/email",
            json={
                "recipient": "Careers@Acme.example",
                "subject": "Machine Learning Engineer application",
                "body": "Dear team,\n\nI hold a PhD in Physics and cut cost by 40% with Redis caching.",
            },
            headers=headers,
        ).json()
        assert edited["email"]["recipient"] == "careers@acme.example"
        assert _claim(edited, "email", "40%")["bucket"] == "verified"
        assert _claim(edited, "email", "PhD")["bucket"] == "needs_you"
        bad = app_client.put(
            f"/api/jobs/{job_id}/application/email",
            json={"recipient": "not-an-email"},
            headers=headers,
        )
        assert bad.status_code == 422

    def test_compose_needs_an_approved_cv(self, app_client: TestClient) -> None:
        headers = _onboard(app_client)
        app_client.post("/api/jobs/fetch", headers=headers)
        [job] = app_client.get("/api/jobs").json()
        app_client.post(f"/api/jobs/{job['id']}/tailor", headers=headers)
        response = app_client.post(f"/api/jobs/{job['id']}/application/compose", headers=headers)
        assert response.status_code == 409 and "Approve your CV" in response.json()["detail"]

    def test_uploads_are_checked(self, app_client: TestClient) -> None:
        headers, job_id = _approved_cv(app_client)
        app_client.post(f"/api/jobs/{job_id}/application/compose", headers=headers)
        fake = app_client.post(
            f"/api/jobs/{job_id}/application/files/transcript",
            files={"file": ("transcript.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")},
            headers=headers,
        )
        assert fake.status_code == 400
        unknown = app_client.post(
            f"/api/jobs/{job_id}/application/files/references",
            files={"file": ("refs.pdf", b"%PDF-1.4\n", "application/pdf")},
            headers=headers,
        )
        assert unknown.status_code == 409
