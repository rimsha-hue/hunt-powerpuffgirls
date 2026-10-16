import smtplib
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx

from app.assets import RECORDED_JOBS
from app.domain.locations import REMOTE, Place
from app.integrations.email import EmailError, OutgoingEmail, SmtpEmailProvider
from app.integrations.jobs.base import JobQuery, JobSourceError
from app.integrations.jobs.sources import (
    ArbeitnowSource,
    JSearchSource,
    RecordedSource,
    RemotiveSource,
)

LAHORE = Place("city:1172451", "city", "Lahore", "PK", "Pakistan")
JSEARCH = "https://jsearch.example/search-v2"
REMOTIVE = "https://remotive.example/api/remote-jobs"
ARBEITNOW = "https://arbeitnow.example/api"


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


def _jsearch_job(**overrides: Any) -> dict[str, Any]:
    job = {
        "job_id": "abc",
        "job_title": "ML Engineer — Lahore",
        "employer_name": "OpsSense",
        "job_apply_link": "https://jobs.example/apply/abc",
        "job_google_link": "https://google.example/abc",
        "job_description": "Build models.",
        "job_is_remote": False,
        "job_posted_at_datetime_utc": "2026-10-12T00:00:00.000Z",
        "job_city": "Lahore",
        "job_state": None,
        "job_country": "PK",
    }
    job.update(overrides)
    return job


class TestJSearch:
    @respx.mock
    async def test_maps_postings_and_sends_place_and_key(self, http: httpx.AsyncClient) -> None:
        route = respx.get(JSEARCH).mock(
            return_value=httpx.Response(200, json={"data": {"jobs": [_jsearch_job()]}})
        )
        source = JSearchSource(http, api_url="https://jsearch.example", api_key="k")
        [job] = await source.search([JobQuery("ML Engineer", LAHORE)])
        request = route.calls.last.request
        assert request.url.params["query"] == "ML Engineer in Lahore, Pakistan"
        assert request.url.params["country"] == "pk"
        assert request.headers["X-RapidAPI-Key"] == "k"
        assert request.headers["X-RapidAPI-Host"] == "jsearch.example"
        assert (job.title, job.location, job.country_code) == (
            "ML Engineer — Lahore",
            "Lahore, PK",
            "PK",
        )
        assert job.apply_url == "https://jobs.example/apply/abc"
        assert job.posted_at is not None and job.posted_at.year == 2026

    @respx.mock
    async def test_remote_query_and_bad_rows_skipped(self, http: httpx.AsyncClient) -> None:
        route = respx.get(JSEARCH).mock(
            return_value=httpx.Response(
                200, json={"data": {"jobs": [_jsearch_job(job_title=""), _jsearch_job()]}}
            )
        )
        source = JSearchSource(http, api_url="https://jsearch.example", api_key="k")
        assert len(await source.search([JobQuery("ML Engineer", REMOTE)])) == 1
        assert route.calls.last.request.url.params["work_from_home"] == "true"

    @respx.mock
    async def test_quota_is_a_source_error(self, http: httpx.AsyncClient) -> None:
        respx.get(JSEARCH).mock(return_value=httpx.Response(429))
        source = JSearchSource(http, api_url="https://jsearch.example", api_key="k")
        with pytest.raises(JobSourceError, match="quota"):
            await source.search([JobQuery("ML Engineer", LAHORE)])

    @respx.mock
    async def test_retries_server_errors(self, http: httpx.AsyncClient) -> None:
        route = respx.get(JSEARCH).mock(
            side_effect=[httpx.Response(503), httpx.Response(200, json={"data": {"jobs": []}})]
        )
        source = JSearchSource(http, api_url="https://jsearch.example", api_key="k")
        assert await source.search([JobQuery("ML Engineer", LAHORE)]) == []
        assert route.call_count == 2


class TestFreeSources:
    @respx.mock
    async def test_remotive_html_becomes_text_and_only_remote_queries_run(
        self, http: httpx.AsyncClient
    ) -> None:
        route = respx.get(REMOTIVE).mock(
            return_value=httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 7,
                            "url": "https://remotive.example/7",
                            "title": "Backend Engineer",
                            "company_name": "Acme",
                            "candidate_required_location": "Worldwide",
                            "publication_date": "2026-10-14T20:33:44",
                            "description": "<p>Use <b>Python</b></p><script>x()</script>",
                        }
                    ]
                },
            )
        )
        source = RemotiveSource(http, api_url=REMOTIVE)
        assert await source.search([JobQuery("Backend Engineer", LAHORE)]) == []
        [job] = await source.search([JobQuery("Backend Engineer", REMOTE)])
        assert route.call_count == 1
        assert job.remote and job.description == "Use Python"
        assert job.posted_at is not None and job.posted_at.tzinfo is not None

    @respx.mock
    async def test_arbeitnow_is_fetched_once_per_run(self, http: httpx.AsyncClient) -> None:
        route = respx.get(ARBEITNOW).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "slug": "x-1",
                            "title": "Data Engineer",
                            "company_name": "Gaia",
                            "location": "Berlin",
                            "remote": True,
                            "url": "https://arbeitnow.example/x-1",
                            "description": "<p>Spark</p>",
                            "created_at": 1792170051,
                        }
                    ]
                },
            )
        )
        source = ArbeitnowSource(http, api_url=ARBEITNOW)
        jobs = await source.search([JobQuery("A", REMOTE), JobQuery("B", LAHORE)])
        assert route.call_count == 1
        assert [j.external_id for j in jobs] == ["x-1"]
        assert jobs[0].posted_at is not None

    @respx.mock
    async def test_network_failure_is_a_source_error(self, http: httpx.AsyncClient) -> None:
        respx.get(ARBEITNOW).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(JobSourceError, match="unreachable"):
            await ArbeitnowSource(http, api_url=ARBEITNOW).search([JobQuery("A", REMOTE)])

    async def test_recorded_postings_load(self) -> None:
        jobs = await RecordedSource(RECORDED_JOBS).search([])
        assert len(jobs) >= 20
        assert all(j.source == "recorded" and j.url.startswith("https://") for j in jobs)


class FakeSMTP:
    instances: list["FakeSMTP"] = []
    fail_login: Exception | None = None
    disconnects = 0

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.sent: list[Any] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def starttls(self, context: object) -> None:
        if FakeSMTP.disconnects:
            FakeSMTP.disconnects -= 1
            raise smtplib.SMTPServerDisconnected("dropped")

    def login(self, user: str, password: str) -> None:
        if FakeSMTP.fail_login:
            raise FakeSMTP.fail_login

    def send_message(self, message: Any) -> None:
        self.sent.append(message)


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.instances = []
    FakeSMTP.fail_login = None
    FakeSMTP.disconnects = 0
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def _provider(redirect_to: str | None = None) -> SmtpEmailProvider:
    return SmtpEmailProvider(
        host="smtp.example",
        port=587,
        user="me@example.com",
        password="app-password",
        timeout=5,
        redirect_to=redirect_to,
    )


class TestSmtp:
    async def test_sends_text_and_html(self, smtp: type[FakeSMTP]) -> None:
        message_id = await _provider().send(
            OutgoingEmail(to="you@example.com", subject="Hi", text="plain", html="<p>rich</p>")
        )
        [sent] = smtp.instances[-1].sent
        assert sent["To"] == "you@example.com"
        assert sent["Message-ID"] == message_id
        assert sent.is_multipart()

    async def test_redirect_for_development(self, smtp: type[FakeSMTP]) -> None:
        await _provider("inbox@example.com").send(OutgoingEmail("you@example.com", "Hi", "t"))
        assert smtp.instances[-1].sent[0]["To"] == "inbox@example.com"

    async def test_retries_a_dropped_connection(self, smtp: type[FakeSMTP]) -> None:
        smtp.disconnects = 1
        await _provider().send(OutgoingEmail("you@example.com", "Hi", "t"))
        assert len(smtp.instances) == 2

    async def test_bad_login_is_not_retried(self, smtp: type[FakeSMTP]) -> None:
        smtp.fail_login = smtplib.SMTPAuthenticationError(535, b"bad")
        with pytest.raises(EmailError, match="rejected the SMTP login"):
            await _provider().send(OutgoingEmail("you@example.com", "Hi", "t"))
        assert len(smtp.instances) == 1
