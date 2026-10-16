"""Adapters for JSearch (RapidAPI), Remotive, Arbeitnow and recorded postings.

Each turns its source's response into JobPostings. None of them decides relevance: the Scout
filters every posting by title and place in code, because the free sources ignore most of the
query.
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog

from app.domain.jobs import JobPosting
from app.integrations.jobs.base import JobQuery, get_json, parse_iso, parse_unix, posting
from app.integrations.webtext import html_to_text

logger = structlog.get_logger()


class JSearchSource:
    """Google for Jobs results through RapidAPI. One request per query (free tier: 200/month)."""

    name = "jsearch"
    live = True

    def __init__(self, http: httpx.AsyncClient, *, api_url: str, api_key: str) -> None:
        self.http = http
        self.api_url = api_url.rstrip("/")
        self.headers = {
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": urlsplit(self.api_url).netloc,
        }

    def _params(self, query: JobQuery) -> dict[str, str]:
        params = {"query": query.role, "date_posted": "month", "num_pages": "1"}
        place = query.place
        if place is None:  # anywhere: the role alone
            return params
        if place.kind == "remote":
            params["work_from_home"] = "true"
            return params
        params["query"] = f"{query.role} in {place.label}"
        if place.country_code:
            params["country"] = place.country_code.lower()
        return params

    async def search(self, queries: Sequence[JobQuery]) -> list[JobPosting]:
        found: list[JobPosting] = []
        for query in queries:
            body = await get_json(
                self.http,
                f"{self.api_url}/search-v2",
                source="JSearch",
                params=self._params(query),
                headers=self.headers,
            )
            data = body.get("data") if isinstance(body, dict) else None
            jobs = data.get("jobs", []) if isinstance(data, dict) else []
            found.extend(p for p in (self._posting(job) for job in jobs) if p)
        return found

    def _posting(self, job: dict[str, Any]) -> JobPosting | None:
        parts = [job.get("job_city"), job.get("job_state"), job.get("job_country")]
        location = ", ".join(str(p) for p in parts if p) or job.get("job_location") or ""
        apply_link = job.get("job_apply_link")
        return posting(
            source=self.name,
            external_id=job.get("job_id"),
            title=job.get("job_title"),
            company=job.get("employer_name"),
            location=location,
            remote=bool(job.get("job_is_remote")),
            url=apply_link or job.get("job_google_link"),
            description=str(job.get("job_description") or ""),
            posted_at=parse_iso(job.get("job_posted_at_datetime_utc")),
            apply_url=apply_link,
            country_code=job.get("job_country"),
        )


class RemotiveSource:
    """Remote jobs only, so it answers remote queries; one request per role."""

    name = "remotive"
    live = True

    def __init__(self, http: httpx.AsyncClient, *, api_url: str) -> None:
        self.http = http
        self.api_url = api_url

    async def search(self, queries: Sequence[JobQuery]) -> list[JobPosting]:
        roles = list(
            dict.fromkeys(q.role for q in queries if q.place is None or q.place.kind == "remote")
        )
        found: list[JobPosting] = []
        for role in roles:
            body = await get_json(
                self.http, self.api_url, source="Remotive", params={"search": role, "limit": "50"}
            )
            jobs = body.get("jobs", []) if isinstance(body, dict) else []
            found.extend(p for p in (self._posting(job) for job in jobs) if p)
        return found

    def _posting(self, job: dict[str, Any]) -> JobPosting | None:
        return posting(
            source=self.name,
            external_id=job.get("id"),
            title=job.get("title"),
            company=job.get("company_name"),
            location=job.get("candidate_required_location") or "Remote",
            remote=True,
            url=job.get("url"),
            description=html_to_text(str(job.get("description") or "")),
            posted_at=parse_iso(job.get("publication_date")),
            apply_url=job.get("url"),
        )


class ArbeitnowSource:
    """A public board (mostly Europe) with no search: one request per run, filtered in code."""

    name = "arbeitnow"
    live = True

    def __init__(self, http: httpx.AsyncClient, *, api_url: str) -> None:
        self.http = http
        self.api_url = api_url

    async def search(self, queries: Sequence[JobQuery]) -> list[JobPosting]:
        if not queries:
            return []
        body = await get_json(self.http, self.api_url, source="Arbeitnow")
        jobs = body.get("data", []) if isinstance(body, dict) else []
        return [p for p in (self._posting(job) for job in jobs) if p]

    def _posting(self, job: dict[str, Any]) -> JobPosting | None:
        return posting(
            source=self.name,
            external_id=job.get("slug"),
            title=job.get("title"),
            company=job.get("company_name"),
            location=job.get("location") or "",
            remote=bool(job.get("remote")),
            url=job.get("url"),
            description=html_to_text(str(job.get("description") or "")),
            posted_at=parse_unix(job.get("created_at")),
            apply_url=job.get("url"),
        )


class RecordedSource:
    """Real postings recorded earlier (fixtures/recorded_jobs.json), for demo mode and as the
    last fallback when every live source fails."""

    name = "recorded"
    live = False

    def __init__(self, path: Path) -> None:
        self.path = path

    async def search(self, queries: Sequence[JobQuery]) -> list[JobPosting]:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        found: list[JobPosting] = []
        for job in raw["jobs"]:
            item = posting(
                source=self.name,
                external_id=job["external_id"],
                title=job["title"],
                company=job["company"],
                location=job["location"],
                remote=bool(job["remote"]),
                url=job["url"],
                description=job["description"],
                posted_at=parse_iso(job.get("posted_at")),
                apply_url=job.get("apply_url"),
                country_code=job.get("country_code"),
            )
            if item:
                found.append(item)
        return found
