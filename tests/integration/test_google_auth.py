import base64
import json
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.integrations.google_oauth import TOKEN_URL
from app.main import create_app
from app.services.signing import sign, unsign
from tests.conftest import LOCATIONS, FakeLLM, signup

CLIENT_ID = "test-client.apps.googleusercontent.com"


def id_token(**overrides: Any) -> str:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "google-sub-123",
        "email": "Hero@Example.com",
        "email_verified": True,
        "name": "Hero Person",
        "exp": int(time.time()) + 300,
        **overrides,
    }
    encode = lambda part: base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{encode({'alg': 'RS256'})}.{encode(claims)}.signature"


@pytest.fixture
def google_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "google_client_id": CLIENT_ID,
            "google_client_secret": SecretStr("test-secret"),
            "app_base_url": "http://testserver",
        }
    )


@pytest.fixture
def gclient(google_settings: Settings) -> Iterator[TestClient]:
    with TestClient(
        create_app(google_settings, llm=FakeLLM(), locations=LOCATIONS), follow_redirects=False
    ) as client:
        yield client


def start(client: TestClient) -> dict[str, str]:
    """Begin the flow; returns the query Google would receive (state, nonce, challenge...)."""
    response = client.get("/api/auth/google/start")
    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    assert location.netloc == "accounts.google.com"
    return {k: v[0] for k, v in parse_qs(location.query).items()}


def finish(client: TestClient, query: dict[str, str], token: str) -> httpx.Response:
    with respx.mock:
        respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"id_token": token}))
        return client.get(
            "/api/auth/google/callback", params={"code": "auth-code", "state": query["state"]}
        )


class TestStart:
    def test_hidden_when_not_configured(self, client: TestClient) -> None:
        assert client.get("/api/meta/config").json()["google_sign_in"] is False
        assert client.get("/api/auth/google/start", follow_redirects=False).status_code == 404

    def test_redirects_to_google_with_pkce_state_and_nonce(self, gclient: TestClient) -> None:
        assert gclient.get("/api/meta/config").json()["google_sign_in"] is True
        query = start(gclient)
        assert query["client_id"] == CLIENT_ID
        assert query["redirect_uri"] == "http://testserver/api/auth/google/callback"
        assert query["code_challenge_method"] == "S256"
        assert query["scope"] == "openid email profile"
        assert len(query["state"]) >= 32 and len(query["nonce"]) >= 32
        assert "ph_oauth" in gclient.cookies


class TestCallback:
    def test_new_user_is_created_and_signed_in(self, gclient: TestClient) -> None:
        query = start(gclient)
        response = finish(gclient, query, id_token(nonce=query["nonce"]))
        assert response.status_code == 302
        assert response.headers["location"] == "/onboarding"
        me = gclient.get("/api/auth/me").json()
        assert me["user"]["email"] == "hero@example.com"
        assert me["user"]["name"] == "Hero Person"
        assert "ph_oauth" not in gclient.cookies  # one-shot

    def test_token_exchange_sends_the_pkce_verifier(self, gclient: TestClient) -> None:
        query = start(gclient)
        with respx.mock:
            route = respx.post(TOKEN_URL).mock(
                return_value=httpx.Response(200, json={"id_token": id_token(nonce=query["nonce"])})
            )
            gclient.get(
                "/api/auth/google/callback", params={"code": "auth-code", "state": query["state"]}
            )
        form = parse_qs(route.calls.last.request.content.decode())
        assert form["grant_type"] == ["authorization_code"]
        assert form["code"] == ["auth-code"]
        assert len(form["code_verifier"][0]) >= 43

    def test_existing_password_account_is_linked_not_duplicated(self, gclient: TestClient) -> None:
        signup(gclient, email="hero@example.com")
        original_id = gclient.get("/api/auth/me").json()["user"]["id"]
        gclient.cookies.clear()

        query = start(gclient)
        finish(gclient, query, id_token(nonce=query["nonce"]))
        assert gclient.get("/api/auth/me").json()["user"]["id"] == original_id

    @pytest.mark.parametrize(
        "claims",
        [
            {"aud": "someone-elses-client"},
            {"iss": "https://evil.example"},
            {"email_verified": False},
            {"exp": int(time.time()) - 3600},
            {"nonce": "replayed-nonce"},
        ],
    )
    def test_untrustworthy_id_tokens_are_rejected(
        self, gclient: TestClient, claims: dict[str, Any]
    ) -> None:
        query = start(gclient)
        token = id_token(**{"nonce": query["nonce"], **claims})
        response = finish(gclient, query, token)
        assert response.headers["location"] == "/login?error=google_failed"
        assert gclient.get("/api/auth/me").status_code == 401

    def test_forged_state_is_rejected(self, gclient: TestClient) -> None:
        query = start(gclient)
        response = finish(
            gclient, {**query, "state": "attacker-state"}, id_token(nonce=query["nonce"])
        )
        assert response.headers["location"] == "/login?error=google_failed"

    def test_callback_without_the_flow_cookie_is_rejected(self, gclient: TestClient) -> None:
        query = start(gclient)
        gclient.cookies.clear()
        response = finish(gclient, query, id_token(nonce=query["nonce"]))
        assert response.headers["location"] == "/login?error=google_failed"

    def test_user_cancelling_at_google(self, gclient: TestClient) -> None:
        start(gclient)
        response = gclient.get("/api/auth/google/callback", params={"error": "access_denied"})
        assert response.headers["location"] == "/login?error=google_cancelled"

    def test_google_only_accounts_have_no_password(self, gclient: TestClient) -> None:
        query = start(gclient)
        finish(gclient, query, id_token(nonce=query["nonce"]))
        gclient.cookies.clear()
        for guess in ("", "password123", "Hero Person"):
            response = gclient.post(
                "/api/auth/login", json={"email": "hero@example.com", "password": guess or "x"}
            )
            assert response.status_code == 401


class TestSignedCookies:
    def test_round_trip_tamper_purpose_and_expiry(self) -> None:
        token = sign({"state": "abc"}, "secret", 60, purpose="google-oauth")
        assert unsign(token, "secret", purpose="google-oauth")["state"] == "abc"  # type: ignore[index]
        assert unsign(token, "other-secret", purpose="google-oauth") is None
        assert unsign(token, "secret", purpose="apply-link") is None
        body, mac = token.split(".")
        assert unsign(f"{body}x.{mac}", "secret", purpose="google-oauth") is None
        assert unsign(sign({}, "secret", -5, purpose="p"), "secret", purpose="p") is None
