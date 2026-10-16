"""GitHubClient.repo_facts against a mocked GitHub API."""

import base64
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.domain.coach import RepoRef
from app.integrations.github import GitHubClient, GitHubRepoNotFoundError

API = "https://api.github.com/repos/ayesha-dev/k8s-demo"
REF = RepoRef("ayesha-dev", "k8s-demo")
REPO = {
    "name": "k8s-demo",
    "html_url": "https://github.com/ayesha-dev/k8s-demo",
    "owner": {"login": "ayesha-dev"},
    "fork": False,
    "private": False,
    "created_at": "2026-10-18T09:00:00Z",
    "description": "FastAPI on a cluster",
    "topics": ["kubernetes"],
}


def _commit(date: str) -> list[dict[str, object]]:
    return [{"commit": {"author": {"date": date}}}]


@pytest.fixture
async def client() -> AsyncIterator[GitHubClient]:
    async with httpx.AsyncClient() as http:
        yield GitHubClient(http)


def _mock_repo(**repo: object) -> None:
    respx.get(API).mock(return_value=httpx.Response(200, json={**REPO, **repo}))
    respx.get(f"{API}/languages").mock(return_value=httpx.Response(200, json={"Python": 900}))
    readme = base64.b64encode(b"Deployed with Kubernetes.").decode()
    respx.get(f"{API}/readme").mock(
        return_value=httpx.Response(200, json={"encoding": "base64", "content": readme})
    )


class TestRepoFacts:
    @respx.mock
    async def test_counts_commits_from_the_last_page_and_dates_the_first(
        self, client: GitHubClient
    ) -> None:
        _mock_repo()
        link = f'<{API}/commits?per_page=1&page=2>; rel="next", <{API}/commits?per_page=1&page=7>; rel="last"'
        respx.get(f"{API}/commits", params={"per_page": "1", "page": "7"}).mock(
            return_value=httpx.Response(200, json=_commit("2026-10-18T09:05:00Z"))
        )
        respx.get(f"{API}/commits", params={"per_page": "1"}).mock(
            return_value=httpx.Response(
                200, json=_commit("2026-10-19T12:00:00Z"), headers={"link": link}
            )
        )
        facts = await client.repo_facts(REF, readme_max_chars=1000)
        assert facts.commit_count == 7
        assert facts.first_commit_at == datetime(2026, 10, 18, 9, 5, tzinfo=UTC)
        assert facts.owner == "ayesha-dev" and facts.languages == ("Python",)
        assert facts.readme == "Deployed with Kubernetes." and facts.topics == ("kubernetes",)

    @respx.mock
    async def test_a_single_commit_and_a_short_readme_cap(self, client: GitHubClient) -> None:
        _mock_repo()
        respx.get(f"{API}/commits").mock(
            return_value=httpx.Response(200, json=_commit("2026-10-18T09:05:00Z"))
        )
        facts = await client.repo_facts(REF, readme_max_chars=8)
        assert facts.commit_count == 1 and facts.readme == "Deployed"

    @respx.mock
    async def test_an_empty_repo_has_no_commits(self, client: GitHubClient) -> None:
        _mock_repo()
        respx.get(f"{API}/commits").mock(return_value=httpx.Response(409, json={}))
        facts = await client.repo_facts(REF, readme_max_chars=1000)
        assert facts.commit_count == 0 and facts.first_commit_at is None

    @respx.mock
    async def test_missing_or_private_repos_are_refused(self, client: GitHubClient) -> None:
        respx.get(API).mock(return_value=httpx.Response(404, json={}))
        with pytest.raises(GitHubRepoNotFoundError):
            await client.repo_facts(REF, readme_max_chars=1000)
        respx.get(API).mock(return_value=httpx.Response(200, json={**REPO, "private": True}))
        with pytest.raises(GitHubRepoNotFoundError, match="public"):
            await client.repo_facts(REF, readme_max_chars=1000)
