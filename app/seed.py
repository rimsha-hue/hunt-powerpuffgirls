"""Seed the demo account if DEMO_USER_EMAIL and DEMO_USER_PASSWORD are set. Idempotent.

Run after migrations: `python -m app.seed`.
"""

import asyncio
import sys

import structlog

from app.config import Settings, get_settings
from app.db.session import create_engine, create_session_factory
from app.domain.taxonomy import default_taxonomy
from app.integrations.llm import build_llm
from app.logging import configure_logging
from app.services.auth import create_user, get_user_by_email
from app.services.ingestion import ingest_resume

logger = structlog.get_logger()

DEMO_ROLES = ["Machine Learning Engineer", "AI Engineer", "Backend Engineer"]
DEMO_LOCATIONS = ["Remote", "Lahore", "Islamabad"]


async def seed(settings: Settings) -> str:
    if not (settings.demo_user_email and settings.demo_user_password):
        return "skipped (DEMO_USER_EMAIL / DEMO_USER_PASSWORD not set)"

    from app.api.ledger import SAMPLE_RESUME

    engine = create_engine(settings.database_url)
    try:
        async with create_session_factory(engine)() as db:
            user = await get_user_by_email(db, settings.demo_user_email)
            if user is not None:
                return "demo user already present"
            user = await create_user(
                db,
                email=settings.demo_user_email,
                password=settings.demo_user_password.get_secret_value(),
                name="Ayesha Khan (demo)",
            )
            user.github_username = settings.demo_github_username
            user.profile.target_roles = DEMO_ROLES
            user.profile.target_locations = DEMO_LOCATIONS
            user.profile.match_threshold = 60
            await ingest_resume(
                db,
                user,
                SAMPLE_RESUME.read_bytes(),
                max_bytes=settings.max_upload_mb * 1024 * 1024,
                llm=build_llm(settings),
                taxonomy=default_taxonomy(),
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


if __name__ == "__main__":
    main()
