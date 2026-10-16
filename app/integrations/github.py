"""GitHub REST API client for public repositories. Timeouts, retries on 429/5xx, typed results."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger()

USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")


class GitHubError(Exception):
    """Safe to show to the user."""


class GitHubUserNotFoundError(GitHubError):
    pass


@dataclass(frozen=True)
class RepoInfo:
    name: str
    html_url: str
    description: str
    language: str | None
    topics: tuple[str, ...]
    fork: bool
    pushed_at: datetime | None
    created_at: datetime | None
    languages: dict[str, int] = field(default_factory=dict)


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class GitHubClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        api_url: str = "https://api.github.com",
        token: str | None = None,
        max_repos: int = 30,
        language_detail_repos: int = 10,
    ) -> None:
        self.http = http
        self.api_url = api_url.rstrip("/")
        self.max_repos = max_repos
        self.language_detail_repos = language_detail_repos
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "power-hunt",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, max=4),
            retry=retry_if_exception(_retryable),
            reraise=True,
        ):
            with attempt:
                response = await self.http.get(
                    f"{self.api_url}{path}", params=params, headers=self.headers
                )
                if (
                    response.status_code == 403
                    and response.headers.get("x-ratelimit-remaining") == "0"
                ):
                    raise GitHubError(
                        "GitHub's rate limit was reached. Try again in a few minutes."
                    )
                response.raise_for_status()
                return response.json()
        raise AssertionError("unreachable")  # pragma: no cover

    async def list_repos(self, username: str) -> list[RepoInfo]:
        if not USERNAME_RE.match(username):
            raise GitHubError("That doesn't look like a GitHub username.")
        try:
            raw = await self._get(
                f"/users/{username}/repos",
                {"per_page": self.max_repos, "sort": "pushed", "type": "owner"},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise GitHubUserNotFoundError(f"GitHub user '{username}' was not found.") from exc
            raise GitHubError("GitHub is not responding right now. Try again shortly.") from exc
        except httpx.TransportError as exc:
            raise GitHubError("GitHub is not reachable right now. Try again shortly.") from exc

        repos = [
            RepoInfo(
                name=r["name"],
                html_url=r["html_url"],
                description=r.get("description") or "",
                language=r.get("language"),
                topics=tuple(r.get("topics") or ()),
                fork=bool(r.get("fork")),
                pushed_at=_parse_time(r.get("pushed_at")),
                created_at=_parse_time(r.get("created_at")),
            )
            for r in raw
            if isinstance(r, dict)
        ]
        owned = [r for r in repos if not r.fork]

        detailed: list[RepoInfo] = []
        for index, repo in enumerate(owned):
            languages: dict[str, int] = {}
            if index < self.language_detail_repos:
                try:
                    languages = await self._get(f"/repos/{username}/{repo.name}/languages")
                except (httpx.HTTPError, GitHubError) as exc:
                    # The primary language from the listing is still usable.
                    logger.info(
                        "github_languages_skipped", repo=repo.name, error_type=type(exc).__name__
                    )
            detailed.append(
                RepoInfo(
                    **{
                        **repo.__dict__,
                        "languages": languages if isinstance(languages, dict) else {},
                    }
                )
            )
        return detailed
