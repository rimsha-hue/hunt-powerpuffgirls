"""The admin console end to end: who gets in, what admins can do to accounts, and what they can't
see."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import LOCATIONS, PASSWORD, FakeLLM, signup
from tests.integration.test_email_verification import Outbox

BOSS = "boss@example.com"


@pytest.fixture
def outbox() -> Outbox:
    return Outbox()


@pytest.fixture
def app(settings: Settings, outbox: Outbox) -> FastAPI:
    configured = settings.model_copy(update={"admin_emails": f" {BOSS.upper()} , ops@example.com"})
    return create_app(configured, llm=FakeLLM(), locations=LOCATIONS, email=outbox)  # type: ignore[arg-type]


@pytest.fixture
def boss(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        client.headers["X-CSRF-Token"] = signup(client, BOSS)
        yield client


def _user(app: FastAPI, email: str = "hero@example.com", *, verify: bool = True) -> TestClient:
    client = TestClient(app)  # same app, its own cookies
    client.headers["X-CSRF-Token"] = signup(client, email, verify=verify)
    return client


def _id(client: TestClient) -> int:
    return int(client.get("/api/auth/me").json()["user"]["id"])


class TestAccess:
    def test_listed_and_verified_means_admin(self, boss: TestClient, app: FastAPI) -> None:
        assert boss.get("/api/auth/me").json()["user"]["role"] == "admin"
        unverified = _user(app, "ops@example.com", verify=False)
        assert unverified.get("/api/auth/me").json()["user"]["role"] == "user"
        assert unverified.get("/api/admin/overview").status_code == 403

    def test_users_and_strangers_are_kept_out(self, boss: TestClient, app: FastAPI) -> None:
        user = _user(app)
        for path in ("/api/admin/overview", "/api/admin/users", "/api/admin/audit"):
            assert user.get(path).status_code == 403
            assert TestClient(app).get(path).status_code == 401
        assert user.post(f"/api/admin/users/{_id(user)}/restore").status_code == 403


class TestConsole:
    def test_overview_and_system_status(self, boss: TestClient, app: FastAPI) -> None:
        _user(app)
        data = boss.get("/api/admin/overview").json()
        assert data["users"] == 2 and data["admins"] == 1 and data["verified"] == 2
        assert data["emails_today"]["verify"] == 2
        assert data["system"]["database"] == "ok" and data["system"]["email"] is True

    def test_the_user_list_shows_accounts_not_their_content(
        self, boss: TestClient, app: FastAPI
    ) -> None:
        _user(app)
        listing = boss.get("/api/admin/users", params={"q": "HERO@"}).json()
        assert listing["total"] == 1
        [row] = listing["users"]
        assert row["email"] == "hero@example.com" and row["verified"] is True
        assert set(row) == {
            "id", "email", "name", "role", "verified", "suspended", "demo", "google_linked",
            "created_at", "last_login_at", "jobs", "applications_sent",
        }  # fmt: skip
        assert boss.get("/api/admin/users", params={"q": "%"}).json()["total"] == 0


class TestAccountActions:
    def test_suspend_blocks_sessions_and_sign_in_until_restored(
        self, boss: TestClient, app: FastAPI
    ) -> None:
        user = _user(app)
        user_id = _id(user)
        suspended = boss.post(f"/api/admin/users/{user_id}/suspend")
        assert suspended.status_code == 200 and suspended.json()["suspended"] is True
        assert user.get("/api/profile").status_code in (401, 403)
        login = TestClient(app).post(
            "/api/auth/login", json={"email": "hero@example.com", "password": PASSWORD}
        )
        assert login.status_code == 403 and "suspended" in login.json()["detail"]
        assert boss.post(f"/api/admin/users/{user_id}/restore").json()["suspended"] is False
        again = TestClient(app).post(
            "/api/auth/login", json={"email": "hero@example.com", "password": PASSWORD}
        )
        assert again.status_code == 200

    def test_sign_out_everywhere(self, boss: TestClient, app: FastAPI) -> None:
        user = _user(app)
        assert boss.post(f"/api/admin/users/{_id(user)}/sign-out").status_code == 200
        assert user.get("/api/profile").status_code == 401

    def test_roles_and_the_last_admin(self, boss: TestClient, app: FastAPI) -> None:
        boss_id = _id(boss)
        assert (
            boss.put(f"/api/admin/users/{boss_id}/role", json={"role": "user"}).status_code == 409
        )
        helper = _user(app)
        promoted = boss.put(f"/api/admin/users/{_id(helper)}/role", json={"role": "admin"})
        assert promoted.json()["role"] == "admin"
        # Now two admins: the helper may demote the boss, but not themself.
        demoted = helper.put(f"/api/admin/users/{boss_id}/role", json={"role": "user"})
        assert demoted.json()["role"] == "user"
        assert boss.get("/api/admin/overview").status_code == 403
        self_demote = helper.put(f"/api/admin/users/{_id(helper)}/role", json={"role": "user"})
        assert self_demote.status_code == 409

    def test_unverified_accounts_cant_be_promoted(self, boss: TestClient, app: FastAPI) -> None:
        user = _user(app, verify=False)
        response = boss.put(f"/api/admin/users/{_id(user)}/role", json={"role": "admin"})
        assert response.status_code == 409

    def test_resend_verification(self, boss: TestClient, app: FastAPI, outbox: Outbox) -> None:
        user = _user(app, verify=False)
        outbox.sent.clear()
        assert boss.post(f"/api/admin/users/{_id(user)}/resend-verification").status_code == 200
        assert [m.subject for m in outbox.sent] == ["Confirm your email for Power Hunt"]

    def test_delete_is_audited_and_guarded(self, boss: TestClient, app: FastAPI) -> None:
        user = _user(app)
        user_id = _id(user)
        assert boss.delete(f"/api/admin/users/{_id(boss)}").status_code == 409
        demo = _user(app, "demo@example.com")
        assert boss.delete(f"/api/admin/users/{_id(demo)}").status_code == 409
        assert boss.delete(f"/api/admin/users/{user_id}").status_code == 204
        assert boss.get("/api/admin/users", params={"q": "hero@"}).json()["total"] == 0
        entries = boss.get("/api/admin/audit", params={"action": "admin_delete"}).json()
        assert entries[0]["entity_id"] == str(user_id) and entries[0]["email"] == BOSS
        assert entries[0]["label"] == "An admin deleted an account"

    def test_run_the_scout_for_everyone(self, boss: TestClient) -> None:
        assert boss.post("/api/admin/scout").status_code == 202


class TestMaintenance:
    def test_admins_can_run_the_clean_up(self, boss: TestClient, app: FastAPI) -> None:
        response = boss.post("/api/admin/maintenance")
        assert response.status_code == 200
        assert set(response.json()) == {"sessions", "links", "email_log", "unverified_accounts"}
        assert _user(app).post("/api/admin/maintenance").status_code == 403
