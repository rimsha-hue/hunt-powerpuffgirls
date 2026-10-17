"""Flow A end to end on SQLite: Fetch now, dedupe, scoring, the digest email, and the signed Apply
link that opens one job (signing the user in for that job only)."""

import re
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.assets import RECORDED_JOBS
from app.config import Settings
from app.domain.jobs import JobPosting
from app.integrations.email import OutgoingEmail
from app.integrations.fetch import FetchHTTPError, FetchResult
from app.integrations.jobs.base import JobQuery, JobSourceError
from app.integrations.jobs.sources import RecordedSource
from app.main import create_app
from app.services.scout import ScoutSources
from tests.conftest import LOCATIONS, FakeLLM, signup

ML_JD = """We build maintenance models.

Requirements
- Python and PyTorch in production
- Docker
Nice to have: Kubernetes

Interested candidates should apply through our careers page."""


def _posting(external_id: str, title: str, **overrides: object) -> JobPosting:
    values: dict[str, object] = {
        "source": "fake",
        "external_id": external_id,
        "title": title,
        "company": "OpsSense",
        "location": "Lahore, PK",
        "remote": False,
        "url": f"https://jobs.example/{external_id}",
        "description": ML_JD,
        "posted_at": datetime.now(UTC),
        "apply_url": f"https://jobs.example/{external_id}/apply",
        "country_code": "PK",
    }
    values.update(overrides)
    return JobPosting(**values)  # type: ignore[arg-type]


class FakeSource:
    live = True

    def __init__(self, name: str, postings: list[JobPosting], fail: bool = False) -> None:
        self.name = name
        self.postings = postings
        self.fail = fail
        self.queries: list[Sequence[JobQuery]] = []

    async def search(self, queries: Sequence[JobQuery]) -> list[JobPosting]:
        self.queries.append(queries)
        if self.fail:
            raise JobSourceError(f"{self.name} is down.")
        return list(self.postings)


class FakeEmail:
    configured = True

    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []

    async def send(self, message: OutgoingEmail) -> str:
        self.sent.append(message)
        return f"<{len(self.sent)}@test>"


POSTINGS = [
    _posting("1", "Machine Learning Engineer"),
    _posting(
        "2",
        "ML Engineer, Remote",
        location="Anywhere",
        remote=True,
        country_code=None,
        description="Send your CV and portfolio to jobs@acme.example. Python required.",
    ),
    _posting("3", "Freelance Copywriter"),  # irrelevant title
    _posting("4", "Machine Learning Engineer", location="Berlin, DE", country_code="DE"),  # place
]


@pytest.fixture
def email() -> FakeEmail:
    return FakeEmail()


@pytest.fixture
def primary() -> FakeSource:
    # The same posting under another source id is a duplicate by company + title + place.
    return FakeSource("fake", [*POSTINGS, _posting("1-copy", "Machine Learning Engineer")])


class JobSites:
    """Each posting's own site: open unless its URL is listed as gone."""

    def __init__(self) -> None:
        self.gone: set[str] = set()

    async def fetch(self, url: str, accept: tuple[str, ...] = ()) -> FetchResult:
        if url in self.gone:
            raise FetchHTTPError(404)
        return FetchResult(url=url, status=200, content_type="text/html", text="Apply here")


@pytest.fixture
def sites() -> JobSites:
    return JobSites()


def _client(
    settings: Settings, sources: ScoutSources, email: FakeEmail, sites: JobSites | None = None
) -> TestClient:
    app = create_app(
        settings,
        llm=FakeLLM(),
        locations=LOCATIONS,
        job_sources=sources,
        email=email,
        fetcher=sites or JobSites(),  # type: ignore[arg-type]
    )
    return TestClient(app)


@pytest.fixture
def jobs_client(
    settings: Settings, primary: FakeSource, email: FakeEmail, sites: JobSites
) -> Iterator[TestClient]:
    sources = ScoutSources([primary], [], RecordedSource(RECORDED_JOBS))
    with _client(settings, sources, email, sites) as client:
        yield client


def _onboard(client: TestClient, threshold: int = 0) -> dict[str, str]:
    headers = {"X-CSRF-Token": signup(client)}
    response = client.put(
        "/api/profile",
        json={
            "target_roles": ["Machine Learning Engineer"],
            "target_locations": ["Lahore, Pakistan", "wfh"],
            "match_threshold": threshold,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert client.post("/api/resume/sample", headers=headers).status_code == 200
    return headers


def _links(email: FakeEmail) -> dict[int, str]:
    found = re.findall(r"/apply/(\d+)\?t=([\w-]+)", email.sent[-1].text)
    return {int(job_id): token for job_id, token in found}


class TestFetch:
    def test_fetch_scores_dedupes_and_sends_one_digest(
        self, jobs_client: TestClient, email: FakeEmail
    ) -> None:
        headers = _onboard(jobs_client)
        run = jobs_client.post("/api/jobs/fetch", headers=headers)
        assert run.status_code == 200, run.text
        body = run.json()
        assert (body["fetched"], body["relevant"], body["duplicates"], body["new_jobs"]) == (
            5,
            3,
            1,
            2,
        )
        assert body["digest_sent"] == 2

        jobs = jobs_client.get("/api/jobs").json()
        assert {j["title"] for j in jobs} == {"Machine Learning Engineer", "ML Engineer, Remote"}
        by_title = {j["title"]: j for j in jobs}
        onsite, remote = by_title["Machine Learning Engineer"], by_title["ML Engineer, Remote"]
        assert onsite["apply_method"] == "form"
        assert remote["apply_method"] == "email"
        assert remote["apply_email"] == "jobs@acme.example"
        assert remote["required_attachments"] == ["cv", "portfolio"]
        assert "Python" in onsite["matched_skills"]
        assert 0 < onsite["match_score"] <= 100 and 0 < onsite["ats_before"] <= 100

        detail = jobs_client.get(f"/api/jobs/{onsite['id']}").json()
        weights = {k["term"]: k["weight"] for k in detail["keywords"]}
        assert weights["Python"] == 2 and weights["Kubernetes"] == 1
        assert set(detail["ats_checks"]) >= {"standard_headings", "contact_info"}

        [digest] = email.sent
        assert digest.subject == "Power Hunt: 2 new jobs for you"
        assert set(_links(email)) == {j["id"] for j in jobs}
        assert "<script" not in (digest.html or "")

    def test_closed_postings_are_never_offered(
        self, jobs_client: TestClient, email: FakeEmail, sites: JobSites
    ) -> None:
        sites.gone.add("https://jobs.example/2/apply")  # applications closed at the source
        headers = _onboard(jobs_client)
        assert jobs_client.post("/api/jobs/fetch", headers=headers).json()["digest_sent"] == 1
        assert [j["title"] for j in jobs_client.get("/api/jobs").json()] == [
            "Machine Learning Engineer"
        ]
        closed = jobs_client.get("/api/jobs?view=closed").json()
        assert [(j["title"], j["status"]) for j in closed] == [("ML Engineer, Remote", "expired")]
        assert set(_links(email)) == {j["id"] for j in jobs_client.get("/api/jobs").json()}

    def test_posted_date_filter(self, jobs_client: TestClient) -> None:
        headers = _onboard(jobs_client)
        jobs_client.post("/api/jobs/fetch", headers=headers)
        today = datetime.now(UTC).date()
        assert len(jobs_client.get(f"/api/jobs?posted_from={today}&posted_to={today}").json()) == 2
        tomorrow = today + timedelta(days=1)
        assert jobs_client.get(f"/api/jobs?posted_from={tomorrow}").json() == []
        backwards = jobs_client.get(f"/api/jobs?posted_from={tomorrow}&posted_to={today}")
        assert backwards.status_code == 422

    def test_second_fetch_adds_nothing_and_sends_nothing(
        self, jobs_client: TestClient, email: FakeEmail
    ) -> None:
        headers = _onboard(jobs_client)
        jobs_client.post("/api/jobs/fetch", headers=headers)
        again = jobs_client.post("/api/jobs/fetch", headers=headers).json()
        assert again["new_jobs"] == 0 and again["duplicates"] == 3
        assert again["digest_sent"] == 0
        assert len(email.sent) == 1

    def test_open_jobs_are_rescored_when_the_ledger_changes(self, jobs_client: TestClient) -> None:
        headers = _onboard(jobs_client)
        jobs_client.post("/api/jobs/fetch", headers=headers)
        before = {j["id"]: j for j in jobs_client.get("/api/jobs").json()}
        python = [
            f
            for f in jobs_client.get("/api/ledger?type=skill").json()
            if f["claim_text"] == "Python"
        ]
        assert python
        for fact in python:
            jobs_client.delete(f"/api/ledger/{fact['id']}", headers=headers)

        run = jobs_client.post("/api/jobs/fetch", headers=headers).json()
        after = {j["id"]: j for j in jobs_client.get("/api/jobs").json()}
        assert run["rescored"] == len(before)
        for job_id, job in after.items():
            assert job["match_score"] < before[job_id]["match_score"]
            assert "Python" in job["missing_skills"]
            assert job["ats_before"] == before[job_id]["ats_before"]  # frozen

    def test_threshold_keeps_weak_matches_out_of_the_digest(
        self, jobs_client: TestClient, email: FakeEmail
    ) -> None:
        headers = _onboard(jobs_client, threshold=100)
        body = jobs_client.post("/api/jobs/fetch", headers=headers).json()
        scores = [j["match_score"] for j in jobs_client.get("/api/jobs").json()]
        assert body["new_jobs"] == 2
        assert body["digest_sent"] == sum(score >= 100 for score in scores) < 2

    def test_needs_target_roles(self, jobs_client: TestClient) -> None:
        headers = {"X-CSRF-Token": signup(jobs_client)}
        response = jobs_client.post("/api/jobs/fetch", headers=headers)
        assert response.status_code == 400
        assert "target role" in response.json()["detail"]

    def test_recorded_jobs_only_when_every_live_source_fails(
        self, settings: Settings, email: FakeEmail
    ) -> None:
        down = FakeSource("fake", [], fail=True)
        sources = ScoutSources(
            [down], [FakeSource("other", [], fail=True)], RecordedSource(RECORDED_JOBS)
        )
        with _client(settings, sources, email) as client:
            headers = _onboard(client)
            body = client.post("/api/jobs/fetch", headers=headers).json()
        assert body["used_recorded"] is True
        assert body["new_jobs"] > 0
        assert [s["error"] is not None for s in body["sources"]] == [True, True, False]

    def test_live_source_with_no_matches_does_not_fall_back_to_recorded(
        self, settings: Settings, email: FakeEmail
    ) -> None:
        sources = ScoutSources([FakeSource("fake", [])], [], RecordedSource(RECORDED_JOBS))
        with _client(settings, sources, email) as client:
            headers = _onboard(client)
            body = client.post("/api/jobs/fetch", headers=headers).json()
        assert body["used_recorded"] is False and body["new_jobs"] == 0


class TestStatusAndGate:
    def test_skip_restore_and_choose(self, jobs_client: TestClient) -> None:
        headers = _onboard(jobs_client)
        jobs_client.post("/api/jobs/fetch", headers=headers)
        job_id = jobs_client.get("/api/jobs").json()[0]["id"]

        skipped = jobs_client.post(
            f"/api/jobs/{job_id}/status", json={"status": "skipped"}, headers=headers
        )
        assert skipped.json()["status"] == "skipped"
        assert job_id not in [j["id"] for j in jobs_client.get("/api/jobs").json()]
        assert [j["id"] for j in jobs_client.get("/api/jobs?view=skipped").json()] == [job_id]

        jobs_client.post(f"/api/jobs/{job_id}/status", json={"status": "new"}, headers=headers)
        chosen = jobs_client.post(f"/api/jobs/{job_id}/apply", headers=headers)
        assert chosen.json()["status"] == "shortlisted"
        again = jobs_client.post(f"/api/jobs/{job_id}/apply", headers=headers)
        assert again.json()["status"] == "shortlisted"

        refused = jobs_client.post(
            f"/api/jobs/{job_id}/status", json={"status": "applied"}, headers=headers
        )
        assert refused.status_code == 409

    def test_pipeline_tracks_picked_and_self_applied_jobs(self, jobs_client: TestClient) -> None:
        headers = _onboard(jobs_client)
        jobs_client.post("/api/jobs/fetch", headers=headers)
        first, second = (j["id"] for j in jobs_client.get("/api/jobs").json())
        assert jobs_client.get("/api/applications").json() == []  # nothing picked yet

        jobs_client.post(f"/api/jobs/{first}/apply", headers=headers)
        marked = jobs_client.post(f"/api/jobs/{second}/applied-elsewhere", headers=headers)
        assert marked.json()["status"] == "applied"
        again = jobs_client.post(f"/api/jobs/{second}/applied-elsewhere", headers=headers)
        assert again.status_code == 409

        stages = {i["job"]["id"]: i for i in jobs_client.get("/api/applications").json()}
        assert stages[first]["stage"] == "picked" and not stages[first]["external"]
        assert stages[second]["stage"] == "applied" and stages[second]["external"]

        jobs_client.post(
            f"/api/jobs/{second}/status", json={"status": "interview"}, headers=headers
        )
        stages = {i["job"]["id"]: i["stage"] for i in jobs_client.get("/api/applications").json()}
        assert stages[second] == "interview"

    def test_users_cannot_see_each_others_jobs(self, jobs_client: TestClient) -> None:
        headers = _onboard(jobs_client)
        jobs_client.post("/api/jobs/fetch", headers=headers)
        job_id = jobs_client.get("/api/jobs").json()[0]["id"]
        jobs_client.cookies.clear()
        other = {"X-CSRF-Token": signup(jobs_client, "villain@example.com")}
        assert jobs_client.get(f"/api/jobs/{job_id}").status_code == 404
        assert jobs_client.post(f"/api/jobs/{job_id}/apply", headers=other).status_code == 404
        marked = jobs_client.post(f"/api/jobs/{job_id}/applied-elsewhere", headers=other)
        assert marked.status_code == 404
        assert jobs_client.get("/api/applications").json() == []


class TestApplyLink:
    def _digest_link(self, client: TestClient, email: FakeEmail) -> tuple[int, int, str]:
        headers = _onboard(client)
        client.post("/api/jobs/fetch", headers=headers)
        links = _links(email)
        job_id, other_id = sorted(links)
        return job_id, other_id, links[job_id]

    def test_link_signs_in_for_that_job_only_and_works_once(
        self, jobs_client: TestClient, email: FakeEmail
    ) -> None:
        job_id, other_id, token = self._digest_link(jobs_client, email)
        jobs_client.cookies.clear()

        opened = jobs_client.get(f"/apply/{job_id}?t={token}", follow_redirects=False)
        assert opened.status_code == 302
        assert opened.headers["location"] == f"/jobs/{job_id}"

        me = jobs_client.get("/api/auth/me").json()
        assert me["scope_job_id"] == job_id
        job = jobs_client.get(f"/api/jobs/{job_id}")
        assert job.status_code == 200 and job.json()["status"] == "shortlisted"
        assert jobs_client.get(f"/api/jobs/{other_id}").status_code == 404
        assert jobs_client.get("/api/jobs").status_code == 403
        assert jobs_client.get("/api/ledger").status_code == 403

        reused = jobs_client.get(f"/apply/{job_id}?t={token}", follow_redirects=False)
        assert reused.headers["location"] == "/login?error=apply_link"

    def test_link_keeps_an_existing_full_session(
        self, jobs_client: TestClient, email: FakeEmail
    ) -> None:
        job_id, _other, token = self._digest_link(jobs_client, email)
        jobs_client.get(f"/apply/{job_id}?t={token}", follow_redirects=False)
        assert jobs_client.get("/api/auth/me").json()["scope_job_id"] is None
        assert jobs_client.get("/api/jobs").status_code == 200

    def test_token_is_bound_to_its_job(self, jobs_client: TestClient, email: FakeEmail) -> None:
        _job_id, other_id, token = self._digest_link(jobs_client, email)
        jobs_client.cookies.clear()
        wrong = jobs_client.get(f"/apply/{other_id}?t={token}", follow_redirects=False)
        assert wrong.headers["location"] == "/login?error=apply_link"
        forged = jobs_client.get(f"/apply/{other_id}?t={'x' * 43}", follow_redirects=False)
        assert forged.headers["location"] == "/login?error=apply_link"


class TestInternalTrigger:
    def test_disabled_without_a_token(self, jobs_client: TestClient) -> None:
        assert jobs_client.post("/internal/run-scout").status_code == 404

    def test_runs_every_user_with_roles(
        self, database_url: str, static_dir: Path, primary: FakeSource, email: FakeEmail
    ) -> None:
        settings = Settings(
            database_url=database_url,
            static_dir=static_dir,
            log_json=False,
            scheduler_enabled=False,
            internal_token="s3cret-token-value",  # type: ignore[arg-type]
            _env_file=None,  # type: ignore[call-arg]
        )
        sources = ScoutSources([primary], [], RecordedSource(RECORDED_JOBS))
        with _client(settings, sources, email) as client:
            _onboard(client)
            wrong = client.post("/internal/run-scout", headers={"X-Internal-Token": "nope"})
            assert wrong.status_code == 404
            ok = client.post(
                "/internal/run-scout", headers={"X-Internal-Token": "s3cret-token-value"}
            )
            assert ok.json() == {"users": 1}
            assert len(client.get("/api/jobs").json()) == 2
