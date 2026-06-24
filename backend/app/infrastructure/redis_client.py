"""Shared async Redis client pool.

Initialized via ``init_redis()`` in application lifespan and torn down via
``close_redis()``.  Use ``get_redis()`` for direct access or inject a
``TaskStore`` via the FastAPI dependency ``TaskStoreDep``.
"""

from __future__ import annotations

import redis.asyncio as aioredis

_pool: aioredis.Redis | None = None


async def init_redis(url: str) -> None:
    global _pool  # noqa: PLW0603
    _pool = aioredis.from_url(url, decode_responses=True)


async def close_redis() -> None:
    global _pool  # noqa: PLW0603
    if _pool is not None:
        await _pool.aclose()
        _pool = None


def get_redis() -> aioredis.Redis:
    if _pool is None:
        raise RuntimeError("Redis pool not initialised — lifespan not running?")
    return _pool
