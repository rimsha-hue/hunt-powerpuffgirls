from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.conftest import LOCATIONS, PASSWORD, signup

PASSWORD_SECRET = SecretStr(PASSWORD)


class TestSignupAndLogin:
    def test_signup_sets_a_hardened_session_cookie(self, client: TestClient) -> None:
        response = client.post(
            "/api/auth/signup", json={"email": "Hero@Example.com", "password": PASSWORD}
        )
        assert response.status_code == 201
        cookie = response.headers["set-cookie"].lower()
        assert "ph_session=" in cookie
        assert "httponly" in cookie
        assert "samesite=lax" in cookie
        body = response.json()
        assert body["user"]["email"] == "hero@example.com"
        assert body["onboarded"] is False
        assert len(body["csrf_token"]) >= 32

    def test_duplicate_email_is_rejected(self, client: TestClient) -> None:
        signup(client)
        response = client.post(
            "/api/auth/signup", json={"email": "hero@example.com", "password": PASSWORD}
        )
        assert response.status_code == 409

    def test_short_passwords_are_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/auth/signup", json={"email": "a@example.com", "password": "short"}
        )
        assert response.status_code == 422

    def test_login_with_correct_and_wrong_password(self, client: TestClient) -> None:
        signup(client)
        client.cookies.clear()
        wrong = client.post(
            "/api/auth/login", json={"email": "hero@example.com", "password": "nope-nope-nope"}
        )
        assert wrong.status_code == 401
        unknown = client.post(
            "/api/auth/login", json={"email": "ghost@example.com", "password": PASSWORD}
        )
        assert unknown.status_code == 401
        assert wrong.json() == unknown.json()  # no account enumeration
        right = client.post(
            "/api/auth/login", json={"email": "hero@example.com", "password": PASSWORD}
        )
        assert right.status_code == 200
        assert client.get("/api/auth/me").status_code == 200

    def test_seeded_accounts_on_reserved_domains_can_log_in(self, settings) -> None:  # type: ignore[no-untyped-def]
        import asyncio
        import sys

        from app.main import create_app
        from app.seed import seed

        demo = settings.model_copy(
            update={"demo_user_email": "judge@demo.test", "demo_user_password": PASSWORD_SECRET}
        )
        loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        assert asyncio.run(seed(demo, LOCATIONS), loop_factory=loop_factory) == "demo user created"
        with TestClient(create_app(demo, locations=LOCATIONS)) as client:
            response = client.post(
                "/api/auth/login", json={"email": "judge@demo.test", "password": PASSWORD}
            )
            assert response.status_code == 200, response.text
            assert response.json()["is_demo"] is True

    def test_login_is_rate_limited(self, client: TestClient) -> None:
        statuses = [
            client.post(
                "/api/auth/login", json={"email": "x@example.com", "password": "whatever-123"}
            ).status_code
            for _ in range(11)
        ]
        assert statuses[:10] == [401] * 10
        assert statuses[10] == 429


class TestSessionsAndCsrf:
    def test_me_requires_a_session(self, client: TestClient) -> None:
        assert client.get("/api/auth/me").status_code == 401

    def test_state_changes_require_the_csrf_token(self, client: TestClient, csrf: str) -> None:
        no_token = client.put("/api/profile", json={"location": "Lahore"})
        bad_token = client.put(
            "/api/profile", json={"location": "Lahore"}, headers={"X-CSRF-Token": "forged"}
        )
        good = client.put(
            "/api/profile", json={"location": "Lahore"}, headers={"X-CSRF-Token": csrf}
        )
        assert no_token.status_code == 403
        assert bad_token.status_code == 403
        assert good.status_code == 200
        assert good.json()["location"]["label"] == "Lahore, Pakistan"

    def test_logout_ends_the_session(self, client: TestClient, csrf: str) -> None:
        assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
        assert client.get("/api/auth/me").status_code == 401

    def test_demo_account_is_flagged(self, client: TestClient) -> None:
        signup(client, email="demo@example.com")
        assert client.get("/api/auth/me").json()["is_demo"] is True


class TestDeleteMyData:
    def test_removes_the_user_and_everything_they_own(self, client: TestClient, csrf: str) -> None:
        headers = {"X-CSRF-Token": csrf}
        assert client.post("/api/resume/sample", headers=headers).status_code == 200
        assert len(client.get("/api/ledger").json()) > 0

        assert client.delete("/api/auth/me", headers=headers).status_code == 204
        assert client.get("/api/auth/me").status_code == 401
        relogin = client.post(
            "/api/auth/login", json={"email": "hero@example.com", "password": PASSWORD}
        )
        assert relogin.status_code == 401

        # The same email can sign up again and starts with an empty ledger.
        signup(client)
        assert client.get("/api/ledger").json() == []


class TestProfile:
    def test_updates_preferences_and_normalises_lists(self, client: TestClient, csrf: str) -> None:
        response = client.put(
            "/api/profile",
            json={
                "github_username": "https://github.com/octocat/",
                "portfolio_url": "https://example.com/me",
                "target_roles": ["AI Engineer", " ai engineer ", "Backend Engineer", ""],
                "match_threshold": 70,
                "calm_mode": True,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["github_username"] == "octocat"
        assert body["target_roles"] == ["AI Engineer", "Backend Engineer"]
        assert body["match_threshold"] == 70
        assert client.get("/api/auth/me").json()["calm_mode"] is True

    def test_rejects_invalid_values(self, client: TestClient, csrf: str) -> None:
        headers = {"X-CSRF-Token": csrf}
        assert (
            client.put(
                "/api/profile", json={"github_username": "bad name!"}, headers=headers
            ).status_code
            == 422
        )
        assert (
            client.put(
                "/api/profile", json={"portfolio_url": "javascript:alert(1)"}, headers=headers
            ).status_code
            == 422
        )
        assert (
            client.put("/api/profile", json={"match_threshold": 101}, headers=headers).status_code
            == 422
        )
