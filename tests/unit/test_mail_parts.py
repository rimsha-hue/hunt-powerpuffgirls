"""Email building blocks: extra headers, the digest's unsubscribe link, the 24-hour send limit."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Job, User
from app.integrations.email import OutgoingEmail, SmtpEmailProvider
from app.services import mailer, unsubscribe
from app.services.notifier import render_digest

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)
SECRET = "unit-test-secret-0123456789abcdef"


def test_extra_headers_reach_the_message() -> None:
    smtp = SmtpEmailProvider(host="h", port=587, user="ph@example.com", password="p", timeout=5)
    headers = unsubscribe.headers("https://ph.example", "tok")
    built = smtp._build(OutgoingEmail(to="a@b.c", subject="s", text="t", headers=headers))
    assert built["List-Unsubscribe"] == "<https://ph.example/api/email/unsubscribe?t=tok>"
    assert built["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_unsubscribe_tokens_name_one_account_and_expire() -> None:
    token = unsubscribe.token(7, SECRET, 30)
    assert unsubscribe.user_id(token, SECRET) == 7
    assert unsubscribe.user_id(token, "another-secret-0123456789abcdefgh") is None
    assert unsubscribe.user_id(token + "x", SECRET) is None


def test_the_digest_carries_an_unsubscribe_link() -> None:
    job = Job(
        id=1, title="ML Engineer", company="Acme", location="Lahore", remote=False,
        match_score=80, ats_before=60, apply_method="form",
    )  # fmt: skip
    url = "https://ph.example/unsubscribe?t=tok"
    _subject, text, html = render_digest([job], {1: "https://ph.example/apply/1"}, 72, url)
    assert url in text and "https://ph.example/unsubscribe?t=tok" in html


@pytest.fixture
async def session(database_url: str):  # type: ignore[no-untyped-def]
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        user = User(email="hero@example.com", name="", password_hash="", created_at=NOW)
        db.add(user)
        await db.flush()
        yield db, user.id
    await engine.dispose()


async def test_the_daily_limit_counts_digests_and_applications_only(session) -> None:  # type: ignore[no-untyped-def]
    db, user_id = session
    mailer.record(db, user_id, "digest", NOW - timedelta(hours=2))
    mailer.record(db, user_id, "application", NOW - timedelta(hours=1))
    mailer.record(db, user_id, "welcome", NOW)  # transactional mail isn't capped here
    mailer.record(db, user_id, "digest", NOW - timedelta(hours=30))  # outside the window
    await db.flush()
    await mailer.check_limit(db, user_id, limit=3, now=NOW)
    with pytest.raises(mailer.SendLimitError):
        await mailer.check_limit(db, user_id, limit=2, now=NOW)
