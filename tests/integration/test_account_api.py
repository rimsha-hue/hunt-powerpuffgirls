"""Accounts and email: welcome once, password reset and change, per-account login throttling,
digest unsubscribe, and the email log."""

import re
import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services import unsubscribe
from tests.conftest import LOCATIONS, PASSWORD, VERIFY_LINK, FakeLLM, signup
from tests.integration.test_email_verification import Outbox

RESET_LINK = re.compile(r"/reset-password\?t=([\w-]+)")
NEW_PASSWORD = "a-brand-new-passphrase"


@pytest.fixture
def outbox() -> Outbox:
    return Outbox()


@pytest.fixture
def app_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"rate_limit_login_account": "3/hour"})


@pytest.fixture
def mail(app_settings: Settings, outbox: Outbox) -> Iterator[TestClient]:
    app = create_app(app_settings, llm=FakeLLM(), locations=LOCATIONS, email=outbox)  # type: ignore[arg-type]
    with TestClient(app) as client:
        yield client


def _login(client: TestClient, password: str, email: str = "hero@example.com") -> int:
    client.cookies.clear()
    return client.post("/api/auth/login", json={"email": email, "password": password}).status_code


def _reset_token(client: TestClient, outbox: Outbox, email: str = "hero@example.com") -> str:
    assert client.post("/api/auth/password/forgot", json={"email": email}).status_code == 202
    message = next(m for m in outbox.sent if m.subject == "Reset your Power Hunt password")
    found = RESET_LINK.search(message.text)
    assert found is not None
    return found.group(1)


class TestWelcome:
    def test_sent_once_when_the_address_is_proven(self, mail: TestClient, outbox: Outbox) -> None:
        mail.post("/api/auth/signup", json={"email": "hero@example.com", "password": PASSWORD})
        assert [m.subject for m in outbox.sent] == ["Confirm your email for Power Hunt"]
        link = VERIFY_LINK.search(outbox.sent[0].text)
        assert link is not None
        mail.get(link.group(0), follow_redirects=False)
        welcome = outbox.sent[-1]
        assert welcome.subject == "Welcome to Power Hunt" and welcome.html
        assert "/onboarding" in welcome.text and welcome.to == "hero@example.com"
        # A second proof of the same address (a password reset link) doesn't welcome again.
        token = _reset_token(mail, outbox)
        mail.post("/api/auth/password/reset", json={"token": token, "password": NEW_PASSWORD})
        assert [m.subject for m in outbox.sent].count("Welcome to Power Hunt") == 1

    def test_not_before_the_address_is_proven(self, mail: TestClient, outbox: Outbox) -> None:
        signup(mail, verify=False)
        assert all(m.subject != "Welcome to Power Hunt" for m in outbox.sent)


class TestPasswordReset:
    def test_reset_signs_out_everywhere_and_works_once(
        self, mail: TestClient, outbox: Outbox
    ) -> None:
        signup(mail)
        token = _reset_token(mail, outbox)
        reset = mail.post(
            "/api/auth/password/reset", json={"token": token, "password": NEW_PASSWORD}
        )
        assert reset.status_code == 204
        assert mail.get("/api/profile").status_code == 401  # the old session is gone
        assert outbox.sent[-1].subject == "Your Power Hunt password was changed"
        assert _login(mail, PASSWORD) == 401
        assert _login(mail, NEW_PASSWORD) == 200
        again = mail.post("/api/auth/password/reset", json={"token": token, "password": "x" * 12})
        assert again.status_code == 400

    def test_a_new_request_cancels_the_old_link(self, mail: TestClient, outbox: Outbox) -> None:
        signup(mail)
        first = _reset_token(mail, outbox)
        outbox.sent.clear()
        second = _reset_token(mail, outbox)
        old = mail.post("/api/auth/password/reset", json={"token": first, "password": NEW_PASSWORD})
        assert old.status_code == 400
        new = mail.post(
            "/api/auth/password/reset", json={"token": second, "password": NEW_PASSWORD}
        )
        assert new.status_code == 204

    def test_unknown_emails_get_the_same_answer_and_no_mail(
        self, mail: TestClient, outbox: Outbox
    ) -> None:
        response = mail.post("/api/auth/password/forgot", json={"email": "nobody@example.com"})
        assert response.status_code == 202 and outbox.sent == []

    def test_a_reset_proves_the_address(self, mail: TestClient, outbox: Outbox) -> None:
        signup(mail, verify=False)
        token = _reset_token(mail, outbox)
        mail.post("/api/auth/password/reset", json={"token": token, "password": NEW_PASSWORD})
        assert _login(mail, NEW_PASSWORD) == 200
        assert mail.get("/api/auth/me").json()["user"]["email_verified"] is True

    def test_the_demo_account_cant_be_reset(self, mail: TestClient, outbox: Outbox) -> None:
        signup(mail, "demo@example.com")
        response = mail.post("/api/auth/password/forgot", json={"email": "demo@example.com"})
        assert response.status_code == 202 and outbox.sent == []


class TestPasswordChange:
    def test_needs_the_current_password_and_keeps_only_this_session(
        self, mail: TestClient, outbox: Outbox
    ) -> None:
        headers = {"X-CSRF-Token": signup(mail)}
        wrong = mail.post(
            "/api/auth/password/change",
            json={"current_password": "not-my-password", "new_password": NEW_PASSWORD},
            headers=headers,
        )
        assert wrong.status_code == 400
        changed = mail.post(
            "/api/auth/password/change",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            headers=headers,
        )
        assert changed.status_code == 204
        assert mail.get("/api/profile").status_code == 200  # this session stays signed in
        assert outbox.sent[-1].subject == "Your Power Hunt password was changed"
        assert _login(mail, NEW_PASSWORD) == 200

    def test_short_passwords_are_refused_per_field(self, mail: TestClient) -> None:
        headers = {"X-CSRF-Token": signup(mail)}
        response = mail.post(
            "/api/auth/password/change",
            json={"current_password": PASSWORD, "new_password": "short"},
            headers=headers,
        )
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["body", "new_password"]

    def test_the_demo_account_cant_change_it(self, mail: TestClient) -> None:
        headers = {"X-CSRF-Token": signup(mail, "demo@example.com")}
        response = mail.post(
            "/api/auth/password/change",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            headers=headers,
        )
        assert response.status_code == 403


class TestLoginThrottle:
    def test_only_failed_sign_ins_count_per_account(self, mail: TestClient) -> None:
        signup(mail)
        assert _login(mail, PASSWORD) == 200  # successes don't use up the allowance
        assert [_login(mail, "wrong-password") for _ in range(3)] == [401, 401, 401]
        blocked = mail.post(
            "/api/auth/login", json={"email": "HERO@example.com", "password": PASSWORD}
        )
        assert blocked.status_code == 429 and "Retry-After" in blocked.headers
        # Another account is unaffected.
        signup(mail, "other@example.com")
        assert _login(mail, PASSWORD, "other@example.com") == 200


class TestUnsubscribe:
    def test_the_signed_link_turns_digests_off(
        self, mail: TestClient, app_settings: Settings
    ) -> None:
        signup(mail)
        assert mail.get("/api/profile").json()["digest_emails"] is True
        user_id = mail.get("/api/auth/me").json()["user"]["id"]
        token = unsubscribe.token(user_id, app_settings.secret_key.get_secret_value(), 30)
        mail.cookies.clear()  # works from a mail app with no session
        assert mail.post(f"/api/email/unsubscribe?t={token}").status_code == 204
        assert _login(mail, PASSWORD) == 200
        assert mail.get("/api/profile").json()["digest_emails"] is False

    def test_a_forged_link_is_refused(self, mail: TestClient) -> None:
        assert mail.post("/api/email/unsubscribe?t=forged.token").status_code == 400

    def test_the_profile_switch(self, mail: TestClient) -> None:
        headers = {"X-CSRF-Token": signup(mail)}
        off = mail.put("/api/profile", json={"digest_emails": False}, headers=headers)
        assert off.status_code == 200 and off.json()["digest_emails"] is False


class TestEmailLog:
    def test_every_email_is_logged_by_kind_only(
        self, mail: TestClient, app_settings: Settings
    ) -> None:
        signup(mail)
        path = app_settings.database_url.split(":///", 1)[1]
        with sqlite3.connect(path) as db:
            columns = [row[1] for row in db.execute("PRAGMA table_info(email_log)")]
            kinds = [row[0] for row in db.execute("SELECT kind FROM email_log ORDER BY id")]
        assert columns == ["id", "user_id", "kind", "created_at"]  # no content is stored
        assert kinds == ["verify", "welcome"]
