"""Alembic environment: async engine, URL from app settings."""

import asyncio
import sys

from alembic import context
from sqlalchemy.engine import Connection

from app.config import get_settings
from app.db.models import Base
from app.db.session import create_engine

target_metadata = Base.metadata


def _url() -> str:
    # Tests and tools may pass a URL explicitly through the Alembic config.
    return context.config.attributes.get("database_url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    # psycopg's async mode needs a selector event loop on Windows (local dev only).
    if sys.platform == "win32":
        asyncio.run(run_migrations_online(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(run_migrations_online())
