"""Redis integration coverage for atomic authentication rate limiting."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any, cast
from uuid import uuid4

import pytest
import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.rate_limit import (
    AuthRateLimiter,
    Bucket,
    RateLimitBackendUnavailableError,
    RateLimitExceededError,
    digest_identifier,
    enforce,
)
from tests.unit.test_auth_rate_limit import _request, _settings

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis_client() -> AsyncGenerator[aioredis.Redis]:
    client = cast(Any, aioredis).from_url(get_settings().redis_url, decode_responses=True)
    await client.ping()
    try:
        yield client
    finally:
        await client.aclose()


async def test_atomic_shared_counters_ttl_expiry_and_cleanup(redis_client: aioredis.Redis) -> None:
    run_id = uuid4().hex
    limiter = AuthRateLimiter(redis_client)
    second_limiter = AuthRateLimiter(redis_client)
    bucket = Bucket(f"integration:{run_id}:login", "shared@example.com", 3, 2)
    other_bucket = Bucket(f"integration:{run_id}:register", "shared@example.com", 3, 2)
    try:
        assert await limiter.reserve(bucket) == 1
        key = limiter._key(bucket)
        assert await redis_client.ttl(key) > 0
        assert await second_limiter.reserve(bucket) == 2
        assert await limiter.reserve(bucket) == 3
        with pytest.raises(RateLimitExceededError) as exc_info:
            await limiter.reserve(bucket)
        assert exc_info.value.retry_after_seconds == await redis_client.ttl(key)
        assert await redis_client.get(key) == "4"
        assert await limiter.reserve(other_bucket) == 1
        assert limiter._key(other_bucket) != key

        results = await asyncio.gather(
            *(
                limiter.reserve(Bucket(bucket.name + ":concurrent", "same", 5, 2))
                for _ in range(12)
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(item, int) for item in results) == 5
        assert sum(isinstance(item, RateLimitExceededError) for item in results) == 7

        await asyncio.sleep(2.2)
        assert await limiter.reserve(bucket) == 1
    finally:
        keys = [
            key
            async for key in redis_client.scan_iter(
                match=f"auth-rate-limit:integration:{run_id}:*"
            )
        ]
        if keys:
            await redis_client.delete(*keys)


async def test_real_redis_outage_fails_closed_or_open(redis_client: aioredis.Redis) -> None:
    await redis_client.aclose()
    unavailable = cast(Any, aioredis).from_url(
        "redis://redis:6399/0", socket_connect_timeout=0.2
    )
    bucket = Bucket(f"integration:{uuid4().hex}:outage", "value", 1, 60)
    with pytest.raises(RateLimitBackendUnavailableError):
        await enforce(_request(), _settings(), unavailable, [bucket])
    assert await enforce(_request(), _settings(), unavailable, [bucket], fail_open=True)
    await unavailable.aclose()


async def test_real_redis_keys_are_opaque_for_all_sensitive_bucket_values(
    redis_client: aioredis.Redis,
) -> None:
    run_id = uuid4().hex
    sensitive = [
        "user@example.com",
        "198.51.100.10",
        "2001:db8::8",
        "198.51.100.10, 10.0.0.2",
        "reset-token",
        "pairing-code",
        "user-123",
        "refresh-token",
        "access-token",
        "Bearer authorization-secret",
        "redis://:password@redis:6379/0",
    ]
    limiter = AuthRateLimiter(redis_client)
    try:
        for index, value in enumerate(sensitive):
            await limiter.reserve(Bucket(f"integration:{run_id}:opaque:{index}", value, 2, 60))
        keys = [
            key
            async for key in redis_client.scan_iter(
                match=f"auth-rate-limit:integration:{run_id}:opaque:*"
            )
        ]
        assert len(keys) == len(sensitive)
        rendered = " ".join(keys)
        assert all(value not in rendered for value in sensitive)
        assert all(digest_identifier(value) in rendered for value in sensitive)
    finally:
        keys = [
            key
            async for key in redis_client.scan_iter(
                match=f"auth-rate-limit:integration:{run_id}:*"
            )
        ]
        if keys:
            await redis_client.delete(*keys)
