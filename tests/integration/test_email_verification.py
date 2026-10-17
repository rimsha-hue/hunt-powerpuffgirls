"""Email ownership: the verification link, sign-in alerts, and nothing sent before verification."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.db.models import AuthSession
from app.integrations.email import OutgoingEmail
from app.main import create_app
from tests.conftest import LOCATIONS, PASSWORD, VERIFY_LINK, FakeLLM, signup


class Outbox:
    configured = True

    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []

    async def send(self, message: OutgoingEmail) -> str:
        self.sent.append(message)
        return f"<{len(self.sent)}@test>"


@pytest.fixture
def outbox() -> Outbox:
    return Outbox()


@pytest.fixture
def mail_client(settings: Settings, outbox: Outbox) -> Iterator[TestClient]:
    app = create_app(settings, llm=FakeLLM(), locations=LOCATIONS, email=outbox)  # type: ignore[arg-type]
    with TestClient(app) as client:
        yield client


def _signup_link(client: TestClient, outbox: Outbox) -> str:
    response = client.post(
        "/api/auth/signup", json={"email": "hero@example.com", "password": PASSWORD}
    )
    assert response.status_code == 201
    [message] = outbox.sent
    assert message.to == "hero@example.com" and "Confirm" in message.subject
    link = VERIFY_LINK.search(message.text)
    assert link is not None
    return link.group(0)


class TestVerification:
    def test_the_link_verifies_once(self, mail_client: TestClient, outbox: Outbox) -> None:
        link = _signup_link(mail_client, outbox)
        assert mail_client.get("/api/auth/me").json()["user"]["email_verified"] is False
        first = mail_client.get(link, follow_redirects=False)
        assert first.headers["location"] == "/profile?verified=1"
        assert mail_client.get("/api/auth/me").json()["user"]["email_verified"] is True
        again = mail_client.get(link, follow_redirects=False)
        assert again.headers["location"] == "/profile?verified=failed"

    def test_a_forged_link_does_nothing(self, mail_client: TestClient, outbox: Outbox) -> None:
        _signup_link(mail_client, outbox)
        forged = mail_client.get("/api/auth/verify?t=not-a-token", follow_redirects=False)
        assert forged.headers["location"] == "/profile?verified=failed"
        assert mail_client.get("/api/auth/me").json()["user"]["email_verified"] is False

    def test_resend_until_verified(self, mail_client: TestClient, outbox: Outbox) -> None:
        csrf = signup(mail_client, verify=False)
        headers = {"X-CSRF-Token": csrf}
        assert mail_client.post("/api/auth/verify/resend", headers=headers).status_code == 202
        [message] = outbox.sent
        link = VERIFY_LINK.search(message.text)
        assert link is not None
        mail_client.get(link.group(0))
        assert mail_client.post("/api/auth/verify/resend", headers=headers).status_code == 409


class TestSignInAlerts:
    def test_a_verified_account_hears_about_each_sign_in(
        self, mail_client: TestClient, outbox: Outbox
    ) -> None:
        signup(mail_client)
        mail_client.cookies.clear()
        login = mail_client.post(
            "/api/auth/login", json={"email": "hero@example.com", "password": PASSWORD}
        )
        assert login.status_code == 200
        [alert] = outbox.sent
        assert alert.subject == "New sign-in to your Power Hunt account"
        assert "password" in alert.text

    def test_the_new_session_is_saved_before_the_alert_goes_out(
        self, mail_client: TestClient, outbox: Outbox
    ) -> None:
        """The response must not wait on the commit: the browser loads the next page at once."""
        signup(mail_client)
        mail_client.cookies.clear()
        factory = mail_client.app.state.session_factory  # type: ignore[attr-defined]
        saved_sessions: list[int] = []

        async def send(message: OutgoingEmail) -> str:
            async with factory() as db:  # a separate connection sees only committed rows
                count = await db.scalar(select(func.count()).select_from(AuthSession))
                saved_sessions.append(int(count or 0))
            outbox.sent.append(message)
            return "<alert@test>"

        outbox.send = send  # type: ignore[method-assign]
        mail_client.post(
            "/api/auth/login", json={"email": "hero@example.com", "password": PASSWORD}
        )
        # Signup's session was ended by clearing cookies only; the login adds a second one.
        assert saved_sessions == [2]
        assert mail_client.get("/api/profile").status_code == 200

    def test_an_unverified_address_gets_no_alerts(
        self, mail_client: TestClient, outbox: Outbox
    ) -> None:
        signup(mail_client, verify=False)
        mail_client.cookies.clear()
        mail_client.post(
            "/api/auth/login", json={"email": "hero@example.com", "password": PASSWORD}
        )
        assert outbox.sent == []
