"""Seed the demo account if DEMO_USER_EMAIL and DEMO_USER_PASSWORD are set. Idempotent.

Run after migrations: `python -m app.seed`. The demo preferences in fixtures/demo_profile.json go
through the same validators as the profile form, so the demo can't hold values a user couldn't.
"""

import asyncio
import json
import sys

import structlog

from app.assets import DEMO_PROFILE, SAMPLE_RESUME
from app.config import Settings, get_settings
from app.db.models import User
from app.db.session import create_engine, create_session_factory
from app.domain.locations import LocationIndex, load_geonames
from app.domain.profile import ProfileRules, normalize_changes
from app.domain.taxonomy import default_taxonomy
from app.integrations.llm import build_llm
from app.logging import configure_logging
from app.services import admin
from app.services.auth import create_user, get_user_by_email
from app.services.ingestion import ingest_resume

logger = structlog.get_logger()

USER_COLUMNS = frozenset({"name", "github_username", "portfolio_url"})


def _apply_demo_profile(user: User, settings: Settings, locations: LocationIndex) -> None:
    raw = json.loads(DEMO_PROFILE.read_text(encoding="utf-8"))
    raw.pop("notes", None)
    if settings.demo_github_username:
        raw["github_username"] = settings.demo_github_username
    values = normalize_changes(raw, ProfileRules(locations, settings.default_phone_region))
    for column, value in values.items():
        setattr(user if column in USER_COLUMNS else user.profile, column, value)


async def grant_admins(settings: Settings) -> int:
    """Listed (ADMIN_EMAILS), verified accounts become admins at startup."""
    if not settings.admin_email_set:
        return 0
    engine = create_engine(settings.database_url, pool_size=1, max_overflow=0)
    try:
        async with create_session_factory(engine)() as db:
            granted = await admin.grant_listed(db, settings.admin_email_set)
            await db.commit()
            return granted
    finally:
        await engine.dispose()


async def seed(settings: Settings, locations: LocationIndex | None = None) -> str:
    if not (settings.demo_user_email and settings.demo_user_password):
        return "skipped (DEMO_USER_EMAIL / DEMO_USER_PASSWORD not set)"

    engine = create_engine(settings.database_url, pool_size=1, max_overflow=0)
    try:
        async with create_session_factory(engine)() as db:
            if await get_user_by_email(db, settings.demo_user_email) is not None:
                return "demo user already present"
            user = await create_user(
                db,
                email=settings.demo_user_email,
                password=settings.demo_user_password.get_secret_value(),
            )
            _apply_demo_profile(user, settings, locations or load_geonames())
            await ingest_resume(
                db,
                user,
                SAMPLE_RESUME.read_bytes(),
                max_bytes=settings.max_upload_mb * 1024 * 1024,
                llm=build_llm(settings),
                taxonomy=default_taxonomy(),
                phone_region=settings.default_phone_region,
            )
            await db.commit()
            return "demo user created"
    finally:
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    outcome = asyncio.run(seed(settings), loop_factory=loop_factory)
    logger.info("seed_finished", outcome=outcome)
    granted = asyncio.run(grant_admins(settings), loop_factory=loop_factory)
    logger.info("admins_granted", count=granted)


if __name__ == "__main__":
    main()
