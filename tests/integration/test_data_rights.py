"""Data rights: the export holds everything the account owns and no secret; the retention clean-up
removes only what is no longer needed."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import AuthSession, EmailLog, EmailToken, User
from app.services.data_rights import SECTIONS, clean
from tests.conftest import signup

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)
OLD = NOW - timedelta(days=40)


class TestExport:
    def test_everything_owned_and_no_secret(self, client: TestClient) -> None:
        headers = {"X-CSRF-Token": signup(client)}
        assert client.post("/api/resume/sample", headers=headers).status_code == 200
        response = client.get("/api/auth/me/export")
        assert response.status_code == 200
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["cache-control"] == "no-store"
        data = response.json()
        assert data["account"]["email"] == "hero@example.com"
        assert {name for name, _ in SECTIONS} <= set(data)
        assert data["resume_lines"] and data["evidence"] and data["profile"]
        text = json.dumps(data)
        for secret in ("password_hash", "token_hash", "csrf_token", "google_sub"):
            assert secret not in text
        activity = client.get("/api/activity").json()["events"]
        assert activity[0]["action"] == "data_exported"

    def test_needs_a_session(self, client: TestClient) -> None:
        assert client.get("/api/auth/me/export").status_code == 401


@pytest.fixture
async def db(database_url: str):  # type: ignore[no-untyped-def]
    engine = create_async_engine(database_url)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


def _user(email: str, **values: object) -> User:
    return User(email=email, name="", password_hash="", **{"created_at": OLD, **values})


async def test_the_clean_up_keeps_what_is_still_needed(db) -> None:  # type: ignore[no-untyped-def]
    stale = _user("stale@example.com", last_login_at=OLD)
    returning = _user("back@example.com", last_login_at=NOW - timedelta(days=2))
    verified = _user("ok@example.com", email_verified_at=OLD, last_login_at=OLD)
    admin = _user("boss@example.com", role="admin", last_login_at=OLD)
    demo = _user("demo@example.com", last_login_at=OLD)
    db.add_all([stale, returning, verified, admin, demo])
    await db.flush()
    db.add_all(
        [
            AuthSession(user_id=verified.id, token_hash="a" * 64, csrf_token="c", expires_at=OLD),
            AuthSession(user_id=verified.id, token_hash="b" * 64, csrf_token="c", expires_at=NOW + timedelta(days=1)),
            EmailToken(user_id=verified.id, purpose="verify_email", token_hash="d" * 64, created_at=OLD, expires_at=OLD + timedelta(days=2)),
            EmailToken(user_id=verified.id, purpose="reset_password", token_hash="e" * 64, created_at=NOW, expires_at=NOW + timedelta(minutes=30)),
            EmailLog(user_id=verified.id, kind="digest", created_at=NOW - timedelta(days=120)),
            EmailLog(user_id=verified.id, kind="digest", created_at=NOW - timedelta(days=1)),
        ]
    )  # fmt: skip
    await db.flush()

    result = await clean(
        db,
        now=NOW,
        unverified_days=30,
        email_log_days=90,
        token_days=7,
        keep_emails=frozenset({"demo@example.com"}),
    )
    assert (result.sessions, result.links, result.email_log, result.unverified_accounts) == (
        1,
        1,
        1,
        1,
    )
    left = set((await db.execute(select(User.email))).scalars())
    assert left == {"back@example.com", "ok@example.com", "boss@example.com", "demo@example.com"}
    assert len((await db.execute(select(EmailToken))).scalars().all()) == 1
