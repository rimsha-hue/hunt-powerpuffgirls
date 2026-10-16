import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.integrations.fetch import SafeFetcher

SITE_IP = "93.184.216.34"
JINA_IP = "104.21.0.1"
PORTFOLIO = "https://janedoe.dev/"

RICH_PAGE = """<html><head><title>Jane</title><script>var secret = "Kubernetes";</script></head><body>
<h2>Skills</h2><p>Python, PyTorch, FastAPI, Docker, PostgreSQL and more tools I use every day at work.</p>
<h2>Projects</h2><h3>MediScan</h3><ul><li>Flutter app reaching 88% identification accuracy on real labels</li></ul>
<h2>Education</h2><p>BS Data Science, NUST</p>
<p>Later I plan to pursue a PhD in robotics, and I write about AI on my blog every week.</p>
<h2>About</h2><p>I build data products end to end, from ingestion and modelling to the dashboards people
actually open on Monday mornings, and I care about tests, observability and clear documentation.</p>
</body></html>"""
JS_SHELL = (
    '<html><head><script src="/app.js"></script></head><body><div id="root"></div></body></html>'
)
JINA_MARKDOWN = """Title: Jane
URL Source: https://janedoe.dev/
Markdown Content:
## Skills
**Python**, [LangChain](https://langchain.com), Qdrant and Groq for retrieval-augmented generation systems in production.
## Projects
### RAG Desk
- Served 1,200 support agents with a retrieval assistant built on LangChain and Qdrant.
"""


@pytest.fixture
def fetching_client(client: TestClient) -> TestClient:
    async def resolve(host: str, port: int) -> list[str]:
        return {
            "janedoe.dev": [SITE_IP],
            "r.jina.ai": [JINA_IP],
            "intranet.example.com": ["10.0.0.7"],
        }[host]

    client.app.state.fetcher = SafeFetcher(client.app.state.http, resolve)  # type: ignore[attr-defined]
    return client


def _set_portfolio(client: TestClient, csrf: str, url: str) -> None:
    response = client.put(
        "/api/profile", json={"portfolio_url": url}, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200, response.text


class TestPortfolioSync:
    @respx.mock
    def test_server_rendered_page_becomes_portfolio_evidence(
        self, fetching_client: TestClient, csrf: str
    ) -> None:
        respx.get(f"https://{SITE_IP}/").mock(return_value=httpx.Response(200, html=RICH_PAGE))
        _set_portfolio(fetching_client, csrf, PORTFOLIO)

        response = fetching_client.post("/api/portfolio/sync", headers={"X-CSRF-Token": csrf})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["method"] == "direct"
        values = {(f["type"], f["normalized_value"]) for f in body["facts"]}
        assert {
            ("skill", "python"),
            ("skill", "pytorch"),
            ("degree", "bachelor|data science"),
        } <= values
        assert ("skill", "kubernetes") not in values  # script contents are not page text
        assert not any(
            t == "degree" and v.startswith("phd") for t, v in values
        )  # a plan, not a degree
        metric = next(f for f in body["facts"] if f["normalized_value"] == "88%")
        assert metric["context"] == "MediScan"
        assert all(
            f["source"] == "portfolio" and f["source_url"] == PORTFOLIO for f in body["facts"]
        )

    @respx.mock
    def test_javascript_pages_fall_back_to_jina_reader(
        self, fetching_client: TestClient, csrf: str
    ) -> None:
        respx.get(f"https://{SITE_IP}/").mock(return_value=httpx.Response(200, html=JS_SHELL))
        jina = respx.get(f"https://{JINA_IP}/https://janedoe.dev/").mock(
            return_value=httpx.Response(
                200, text=JINA_MARKDOWN, headers={"content-type": "text/plain"}
            )
        )
        _set_portfolio(fetching_client, csrf, PORTFOLIO)

        body = fetching_client.post("/api/portfolio/sync", headers={"X-CSRF-Token": csrf}).json()
        assert body["method"] == "rendered"
        assert jina.called
        assert jina.calls.last.request.headers["host"] == "r.jina.ai"
        values = {(f["type"], f["normalized_value"]) for f in body["facts"]}
        assert {("skill", "langchain"), ("skill", "qdrant"), ("metric", "1200 support")} <= values
        assert not any("**" in f["claim_text"] or "](" in f["claim_text"] for f in body["facts"])

    def test_non_public_hosts_are_refused_when_saved(
        self, fetching_client: TestClient, csrf: str
    ) -> None:
        for url in ("https://intranet.local/admin", "http://10.0.0.7/", "http://localhost:8000/"):
            response = fetching_client.put(
                "/api/profile", json={"portfolio_url": url}, headers={"X-CSRF-Token": csrf}
            )
            assert response.status_code == 422, url
            assert response.json()["detail"][0]["loc"] == ["body", "portfolio_url"]

    def test_names_that_resolve_to_private_addresses_are_refused(
        self, fetching_client: TestClient, csrf: str
    ) -> None:
        _set_portfolio(fetching_client, csrf, "https://intranet.example.com/admin")
        response = fetching_client.post("/api/portfolio/sync", headers={"X-CSRF-Token": csrf})
        assert response.status_code == 400
        assert "public internet" in response.json()["detail"]

    @respx.mock
    def test_resync_replaces_portfolio_facts(self, fetching_client: TestClient, csrf: str) -> None:
        respx.get(f"https://{SITE_IP}/").mock(return_value=httpx.Response(200, html=RICH_PAGE))
        _set_portfolio(fetching_client, csrf, PORTFOLIO)
        headers = {"X-CSRF-Token": csrf}
        first = fetching_client.post("/api/portfolio/sync", headers=headers).json()["facts"]
        fetching_client.post("/api/portfolio/sync", headers=headers)
        ledger = fetching_client.get("/api/ledger", params={"source": "portfolio"}).json()
        assert len(ledger) == len(first)

    def test_requires_a_portfolio_url(self, fetching_client: TestClient, csrf: str) -> None:
        response = fetching_client.post("/api/portfolio/sync", headers={"X-CSRF-Token": csrf})
        assert response.status_code == 400
