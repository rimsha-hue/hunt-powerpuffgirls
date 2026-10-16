"""Company research source: Tavily search (free tier, 1,000 credits a month). Timeouts come from
the shared HTTP client; 429 and 5xx are retried with backoff."""

from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential


class ResearchError(Exception):
    """Safe to show to the user."""


@dataclass(frozen=True)
class SearchResult:
    url: str
    content: str


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


class TavilyClient:
    def __init__(
        self, http: httpx.AsyncClient, *, api_url: str, api_key: str, max_results: int
    ) -> None:
        self.http = http
        self.api_url = api_url
        self.api_key = api_key
        self.max_results = max_results

    async def _post(self, body: dict[str, Any]) -> Any:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, max=4),
            retry=retry_if_exception(_retryable),
            reraise=True,
        ):
            with attempt:
                response = await self.http.post(
                    self.api_url, json=body, headers={"Authorization": f"Bearer {self.api_key}"}
                )
                response.raise_for_status()
                return response.json()
        raise AssertionError("unreachable")  # pragma: no cover

    async def search(self, company: str) -> list[SearchResult]:
        try:
            data = await self._post(
                {
                    "query": f'"{company}" about mission products news',
                    "max_results": self.max_results,
                    "search_depth": "basic",
                }
            )
        except (httpx.HTTPError, ValueError) as exc:
            raise ResearchError("Company search is unavailable right now.") from exc
        results = data.get("results") if isinstance(data, dict) else None
        return [
            SearchResult(str(r.get("url") or ""), str(r.get("content") or ""))
            for r in results or []
            if isinstance(r, dict) and str(r.get("url") or "").startswith("https://")
        ]
