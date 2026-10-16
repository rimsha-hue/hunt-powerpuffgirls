"""The health probe must never cancel a database call midway, and must not pile up checks."""

import asyncio

from app.db.session import DatabaseProbe


class SlowCheck:
    def __init__(self, seconds: float, error: Exception | None = None) -> None:
        self.seconds = seconds
        self.error = error
        self.calls = 0
        self.completed = 0
        self.cancelled = 0

    async def __call__(self) -> None:
        self.calls += 1
        try:
            await asyncio.sleep(self.seconds)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if self.error:
            raise self.error
        self.completed += 1


async def test_a_timed_out_check_still_finishes_instead_of_being_cancelled() -> None:
    slow = SlowCheck(0.1)
    probe = DatabaseProbe(slow)
    assert await probe.check(timeout=0.01) == "timeout"
    await asyncio.sleep(0.15)
    assert (slow.completed, slow.cancelled) == (1, 0)
    assert await probe.check(timeout=1) is None


async def test_concurrent_checks_share_one_call() -> None:
    slow = SlowCheck(0.05)
    probe = DatabaseProbe(slow)
    results = await asyncio.gather(*(probe.check(timeout=1) for _ in range(5)))
    assert results == [None] * 5
    assert slow.calls == 1


async def test_failures_are_reported_by_type_and_retried_next_time() -> None:
    failing = SlowCheck(0, error=ConnectionError("db down"))
    probe = DatabaseProbe(failing)
    assert await probe.check(timeout=1) == "ConnectionError"
    assert await probe.check(timeout=1) == "ConnectionError"
    assert failing.calls == 2  # a finished check is not reused
