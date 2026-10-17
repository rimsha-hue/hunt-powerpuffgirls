"""Closed postings: what a closed page says, how each site is asked, and what happens to the job."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.db.models import Job
from app.domain.claims import is_intent
from app.domain.jobs import page_says_closed
from app.integrations.fetch import FetchError, FetchHTTPError, FetchResult
from app.integrations.forms import lever_ids
from app.integrations.liveness import check_posting
from app.services import liveness

NOW = datetime(2026, 10, 17, 8, tzinfo=UTC)
RECHECK = timedelta(hours=12)


class Site:
    """Answers every fetch the same way and records what was asked."""

    def __init__(self, *, status: int = 200, text: str = "", error: bool = False) -> None:
        self.status, self.text, self.error = status, text, error
        self.asked: list[str] = []

    async def fetch(self, url: str, accept: tuple[str, ...] = ()) -> FetchResult:
        self.asked.append(url)
        if self.error:
            raise FetchError("Could not connect to the site.")
        if self.status >= 400:
            raise FetchHTTPError(self.status)
        return FetchResult(url=url, status=self.status, content_type="text/html", text=self.text)


class FakeDb:
    def add(self, _: Any) -> None:
        pass


def job(**overrides: Any) -> Job:
    fields: dict[str, Any] = {
        "id": 1,
        "user_id": 1,
        "status": "new",
        "url": "https://example.com/jobs/1",
        "apply_url": None,
        "expires_at": None,
        "open_checked_at": None,
        "match_score": 70,
    }
    return Job(**(fields | overrides))


class TestClosedWording:
    @pytest.mark.parametrize(
        "text",
        [
            "This job is no longer accepting applications.",
            "Sorry, this position has been filled.",
            "The posting has expired",
            "Applications are now closed for this role.",
            "We are not currently accepting applications.",
        ],
    )
    def test_closed_pages(self, text: str) -> None:
        assert page_says_closed(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Apply now. We review applications on a rolling basis.",
            "Experience with closed-loop control systems",
            "You will own the role end to end.",
        ],
    )
    def test_open_pages(self, text: str) -> None:
        assert not page_says_closed(text)


class TestCheckPosting:
    async def test_greenhouse_is_asked_through_its_api(self) -> None:
        site = Site(status=404)
        url = "https://boards.greenhouse.io/acme/jobs/123"
        assert await check_posting(url, site) == "closed"  # type: ignore[arg-type]
        assert site.asked == ["https://boards-api.greenhouse.io/v1/boards/acme/jobs/123"]

    async def test_lever_is_asked_through_its_api(self) -> None:
        site = Site(text="{}")
        posting = "5ac21346-8e0c-4494-8e7a-3eb92ff77902"
        url = f"https://jobs.lever.co/acme/{posting}"
        assert lever_ids(url) == ("acme", posting)
        assert await check_posting(url, site) == "open"  # type: ignore[arg-type]
        assert site.asked == [f"https://api.lever.co/v0/postings/acme/{posting}"]

    @pytest.mark.parametrize(
        ("site", "verdict"),
        [
            (Site(text="Apply for this role"), "open"),
            (Site(text="This job is no longer available"), "closed"),
            (Site(status=410), "closed"),
            (Site(status=403), "unknown"),  # a login wall says nothing about the job
            (Site(error=True), "unknown"),
        ],
    )
    async def test_other_pages(self, site: Site, verdict: str) -> None:
        assert await check_posting("https://jobs.example.com/1", site) == verdict  # type: ignore[arg-type]


class TestRefresh:
    async def test_past_expiry_closes_without_asking(self) -> None:
        site = Site()
        found = job(expires_at=NOW - timedelta(days=1))
        await liveness.refresh(FakeDb(), found, site, now=NOW, recheck=RECHECK)  # type: ignore[arg-type]
        assert found.status == "expired" and site.asked == []

    async def test_gone_site_closes_and_open_site_is_remembered(self) -> None:
        gone, live = job(id=1), job(id=2)
        await liveness.refresh(FakeDb(), gone, Site(status=404), now=NOW, recheck=RECHECK)  # type: ignore[arg-type]
        await liveness.refresh(FakeDb(), live, Site(text="Apply"), now=NOW, recheck=RECHECK)  # type: ignore[arg-type]
        assert gone.status == "expired"
        assert (live.status, live.open_checked_at) == ("new", NOW)

    async def test_unreadable_site_changes_nothing(self) -> None:
        found = job()
        await liveness.refresh(FakeDb(), found, Site(error=True), now=NOW, recheck=RECHECK)  # type: ignore[arg-type]
        assert (found.status, found.open_checked_at) == ("new", None)

    async def test_recently_checked_and_applied_jobs_are_not_asked(self) -> None:
        site = Site(status=404)
        fresh = job(open_checked_at=NOW - timedelta(hours=1))
        applied = job(id=2, status="applied")
        await liveness.refresh_many(
            FakeDb(),  # type: ignore[arg-type]
            [fresh, applied],
            site,  # type: ignore[arg-type]
            now=NOW,
            recheck=RECHECK,
            limit=10,
            concurrency=2,
        )
        assert site.asked == [] and (fresh.status, applied.status) == ("new", "applied")

    async def test_best_matches_are_checked_first_within_the_limit(self) -> None:
        site = Site(text="Apply")
        jobs = [job(id=i, match_score=s, url=f"https://x.com/{i}") for i, s in [(1, 40), (2, 90)]]
        await liveness.refresh_many(
            FakeDb(),  # type: ignore[arg-type]
            jobs,
            site,  # type: ignore[arg-type]
            now=NOW,
            recheck=RECHECK,
            limit=1,
            concurrency=1,
        )
        assert site.asked == ["https://x.com/2"]


class TestIntent:
    @pytest.mark.parametrize(
        "clause",
        [
            "I look forward to discussing how my background can support your AI engineering roles",
            "I would welcome the chance to talk",
            "Thank you for your time",
            "I'm eager to contribute",
        ],
    )
    def test_intentions_are_not_claims(self, clause: str) -> None:
        assert is_intent(clause)

    @pytest.mark.parametrize(
        "clause", ["I built a RAG service used by 1,200 agents", "Led a team of five engineers"]
    )
    def test_statements_of_record_are(self, clause: str) -> None:
        assert not is_intent(clause)
