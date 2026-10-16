"""Async database engine and session factory."""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class DatabaseProbe:
    """Reports whether the database answers, without ever cancelling a database call midway.

    Cancelling psycopg while it sets up a connection leaves that connection unusable, so a slow
    database (a serverless one waking up, a distant region) would turn every timed-out health
    check into a broken pool. Here the check runs as its own task: callers wait on it with a
    timeout, and when they give up the check still finishes and returns its connection cleanly.
    Concurrent callers share the check in flight instead of piling up new ones.
    """

    def __init__(self, check: Callable[[], Coroutine[Any, Any, object]]) -> None:
        self._check = check
        self._task: asyncio.Task[object] | None = None

    def start(self) -> "asyncio.Task[object]":
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._check())
            # Retrieve the outcome even if nobody is waiting any more (no "never retrieved" noise).
            self._task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        return self._task

    async def check(self, timeout: float) -> str | None:
        """None when the database answered in time, else a short reason ("timeout", error type)."""
        try:
            await asyncio.wait_for(asyncio.shield(self.start()), timeout)
            return None
        except TimeoutError:
            return "timeout"
        except Exception as exc:
            return type(exc).__name__

    async def close(self, timeout: float = 5.0) -> None:
        """Let a check in flight finish (bounded) rather than cancel it mid-connect."""
        if self._task is not None and not self._task.done():
            await asyncio.wait({self._task}, timeout=timeout)


def create_engine(
    database_url: str, *, pool_size: int = 5, max_overflow: int = 5, connect_timeout: int = 10
) -> AsyncEngine:
    if database_url.startswith("sqlite"):
        engine = create_async_engine(database_url)

        # SQLite ignores ON DELETE CASCADE unless foreign keys are enabled per connection.
        @event.listens_for(engine.sync_engine, "connect")
        def _enable_foreign_keys(dbapi_conn: Any, _record: Any) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    # Neon's free tier suspends idle compute: pre_ping avoids handing out dead connections, and
    # recycling stays under Neon's idle timeout.
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=300,
        # The driver's own timeout bounds connection setup, so nothing has to cancel it.
        connect_args={"connect_timeout": connect_timeout},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping(engine: AsyncEngine) -> bool:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True
