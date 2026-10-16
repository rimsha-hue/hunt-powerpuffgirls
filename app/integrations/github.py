"""GitHub REST API client for public repositories. Timeouts, retries on 429/5xx, typed results."""

import base64
import binascii
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

from app.domain.coach import RepoFacts, RepoRef

logger = structlog.get_logger()

USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")


class GitHubError(Exception):
    """Safe to show to the user."""


class GitHubUserNotFoundError(GitHubError):
    pass


class GitHubRepoNotFoundError(GitHubError):
    pass


_LAST_PAGE = re.compile(r'[?&]page=(\d+)[^>]*>;\s*rel="last"')


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
        return (await self._response(path, params)).json()

    async def _response(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
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
                return response
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

    async def repo_facts(self, ref: RepoRef, *, readme_max_chars: int) -> RepoFacts:
        """One public repository: owner, dates, commit count, languages, topics, README."""
        base = f"/repos/{ref.owner}/{ref.name}"
        try:
            repo = await self._get(base)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise GitHubRepoNotFoundError(
                    "That repository was not found. Check the link, and make the repo public."
                ) from exc
            raise GitHubError("GitHub is not responding right now. Try again shortly.") from exc
        except httpx.TransportError as exc:
            raise GitHubError("GitHub is not reachable right now. Try again shortly.") from exc
        if not isinstance(repo, dict) or repo.get("private"):
            raise GitHubRepoNotFoundError("Make the repository public so it can be checked.")
        try:
            languages = await self._get(f"{base}/languages")
            count, first = await self._commits(base)
            readme = await self._readme(base, readme_max_chars)
        except httpx.HTTPError as exc:
            raise GitHubError("GitHub is not responding right now. Try again shortly.") from exc
        owner = repo.get("owner") or {}
        return RepoFacts(
            owner=str(owner.get("login") or ""),
            name=str(repo.get("name") or ref.name),
            html_url=str(repo.get("html_url") or ref.url),
            fork=bool(repo.get("fork")),
            created_at=_parse_time(repo.get("created_at")),
            first_commit_at=first,
            commit_count=count,
            languages=tuple(languages) if isinstance(languages, dict) else (),
            topics=tuple(str(t) for t in repo.get("topics") or ()),
            description=str(repo.get("description") or ""),
            readme=readme,
        )

    async def _commits(self, base: str) -> tuple[int, datetime | None]:
        """(commit count, date of the first commit). One commit per page, so the last page's
        number is the count and its only commit is the first one."""
        try:
            response = await self._response(f"{base}/commits", {"per_page": 1})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:  # an empty repository
                return 0, None
            raise
        last = _LAST_PAGE.search(response.headers.get("link", ""))
        page = response.json()
        if last is not None and int(last.group(1)) > 1:
            count = int(last.group(1))
            page = await self._get(f"{base}/commits", {"per_page": 1, "page": count})
        else:
            count = len(page) if isinstance(page, list) else 0
        first = page[0] if isinstance(page, list) and page else {}
        commit = first.get("commit") or {} if isinstance(first, dict) else {}
        when = (commit.get("author") or {}).get("date") or (commit.get("committer") or {}).get(
            "date"
        )
        return count, _parse_time(when)

    async def _readme(self, base: str, max_chars: int) -> str:
        try:
            data = await self._get(f"{base}/readme")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return ""
            raise
        if not isinstance(data, dict) or data.get("encoding") != "base64":
            return ""
        try:
            raw = base64.b64decode(str(data.get("content") or ""))
        except (binascii.Error, ValueError):
            return ""
        return raw.decode("utf-8", errors="replace")[:max_chars]
