"""Rate limiting as a FastAPI dependency.

Each app instance owns a limiter built from its settings, so limits and storage are configuration:
`memory://` for a single process, a shared store such as `redis://` for several workers or replicas.
The client key is the connection address, which uvicorn takes from X-Forwarded-For only when the
proxy is listed in FORWARDED_ALLOW_IPS, so the header can't be spoofed to dodge limits.
"""

import math
import time
from collections.abc import Awaitable, Callable, Mapping

from fastapi import HTTPException, Request, status
from limits import RateLimitItem, parse_many
from limits.aio.storage import Storage
from limits.aio.strategies import MovingWindowRateLimiter
from limits.storage import storage_from_string


class RateLimiter:
    def __init__(self, storage_uri: str, rules: Mapping[str, str]) -> None:
        uri = storage_uri if storage_uri.startswith("async+") else f"async+{storage_uri}"
        storage = storage_from_string(uri)
        if not isinstance(storage, Storage):
            raise ValueError(f"Unsupported rate limit storage: {storage_uri}")
        self._storage = storage
        self._strategy = MovingWindowRateLimiter(storage)
        self._rules: dict[str, list[RateLimitItem]] = {
            name: parse_many(rule) for name, rule in rules.items()
        }

    async def hit(self, rule: str, key: str) -> int | None:
        """Record one hit. Returns None if allowed, else seconds until the client may retry."""
        for item in self._rules[rule]:
            if not await self._strategy.hit(item, rule, key):
                stats = await self._strategy.get_window_stats(item, rule, key)
                return max(1, math.ceil(stats.reset_time - time.time()))
        return None

    async def blocked(self, rule: str, key: str) -> int | None:
        """Like `hit` without recording one: for limits that count only failures."""
        for item in self._rules[rule]:
            if not await self._strategy.test(item, rule, key):
                stats = await self._strategy.get_window_stats(item, rule, key)
                return max(1, math.ceil(stats.reset_time - time.time()))
        return None

    async def reset(self) -> None:
        await self._storage.reset()


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limit(rule: str) -> Callable[[Request], Awaitable[None]]:
    """Dependency enforcing the named rule from settings (login, signup, oauth, ingest, lookup)."""

    async def dependency(request: Request) -> None:
        limiter: RateLimiter = request.app.state.rate_limiter
        retry_after = await limiter.hit(rule, client_key(request))
        if retry_after is not None:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many attempts. Wait a moment and try again.",
                headers={"Retry-After": str(retry_after)},
            )

    dependency.__name__ = f"rate_limit_{rule}"
    return dependency
