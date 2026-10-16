"""The job source interface and the HTTP plumbing every source shares (timeouts, retries)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.domain.jobs import (
    MAX_COMPANY,
    MAX_DESCRIPTION,
    MAX_LOCATION,
    MAX_TITLE,
    MAX_URL,
    JobPosting,
    clean_line,
    clip,
)
from app.domain.locations import Place


class JobSourceError(Exception):
    """A source failed (down, quota reached, bad response). Safe to show to the user."""


@dataclass(frozen=True)
class JobQuery:
    role: str
    place: Place | None  # a city, a country, REMOTE, or None for anywhere


class JobSource(Protocol):
    name: str
    # False for recorded postings: they are kept regardless of age.
    live: bool

    async def search(self, queries: Sequence[JobQuery]) -> list[JobPosting]: ...


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


async def get_json(
    http: httpx.AsyncClient,
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """GET with retries on 429/5xx and network errors; failures become JobSourceError."""
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, max=4),
            retry=retry_if_exception(_retryable),
            reraise=True,
        ):
            with attempt:
                response = await http.get(url, params=params, headers=headers)
                response.raise_for_status()
                return response.json()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 429:
            raise JobSourceError(f"{source} rate limit or monthly quota reached.") from exc
        if code in (401, 403):
            raise JobSourceError(f"{source} rejected our API key or subscription.") from exc
        raise JobSourceError(f"{source} returned an error ({code}).") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise JobSourceError(f"{source} is unreachable right now.") from exc
    raise AssertionError("unreachable")  # pragma: no cover


def posting(
    *,
    source: str,
    external_id: Any,
    title: Any,
    company: Any,
    location: Any,
    remote: bool,
    url: Any,
    description: str,
    posted_at: datetime | None,
    apply_url: Any = None,
    country_code: Any = None,
) -> JobPosting | None:
    """A JobPosting with every field cleaned and capped, or None if an essential is missing."""
    title_text = clean_line(str(title or ""))
    url_text = str(url or "").strip()
    if not external_id or not title_text or not url_text.startswith(("https://", "http://")):
        return None
    apply = str(apply_url or "").strip()
    return JobPosting(
        source=source,
        external_id=clip(str(external_id), 200),
        title=clip(title_text, MAX_TITLE),
        company=clip(clean_line(str(company or "")) or "Unknown company", MAX_COMPANY),
        location=clip(clean_line(str(location or "")), MAX_LOCATION),
        remote=remote,
        url=url_text[:MAX_URL],
        description=clip(description.replace("�", "-"), MAX_DESCRIPTION),
        posted_at=posted_at,
        apply_url=apply[:MAX_URL] if apply.startswith(("https://", "http://")) else None,
        country_code=str(country_code).upper()[:2] if country_code else None,
    )


def parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_unix(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
