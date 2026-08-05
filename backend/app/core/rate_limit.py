"""Redis-backed abuse controls for authentication endpoints."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from typing import Any, cast

import redis.asyncio as aioredis
from fastapi import Request

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger("auth_rate_limit")

KEY_PREFIX = "auth-rate-limit:"

_INCREMENT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then redis.call('EXPIRE', KEYS[1], ARGV[2]) end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""

_CLEAR_IF_UNCHANGED_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if current ~= false and tonumber(current) == tonumber(ARGV[1]) then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class RateLimitBackendUnavailableError(RuntimeError):
    """Raised when Redis cannot evaluate an abuse-control decision."""


class RateLimitExceededError(RuntimeError):
    """Raised when one or more abuse-control buckets are exhausted."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(retry_after_seconds, 1)
        super().__init__("authentication rate limit exceeded")


@dataclass(frozen=True)
class Bucket:
    name: str
    value: str
    limit: int
    window_seconds: int


@dataclass(frozen=True)
class BucketSnapshot:
    key: str
    count: int


def digest_identifier(value: str) -> str:
    """Return an opaque, stable digest suitable for Redis keys/log fields."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _parse_networks(values: list[str]) -> list[Any]:
    networks: list[Any] = []
    for value in values:
        try:
            networks.append(ip_network(value.strip(), strict=False))
        except ValueError:
            logger.warning("auth_proxy_cidr_invalid", value_digest=digest_identifier(value))
    return networks


def client_ip(request: Request, settings: Settings) -> str:
    """Resolve a client IP without trusting arbitrary forwarded headers."""
    peer = request.client.host if request.client is not None else "unknown"
    try:
        peer_address = ip_address(peer)
    except ValueError:
        return "unknown"

    trusted = _parse_networks(settings.trusted_proxy_cidrs)
    if not any(peer_address in network for network in trusted):
        return str(peer_address)

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return str(peer_address)
    parts = [part.strip() for part in forwarded.split(",")]
    if not parts or any(not part for part in parts):
        return str(peer_address)
    try:
        addresses = [ip_address(part) for part in parts]
    except ValueError:
        return str(peer_address)

    # Walk from the authenticated direct peer toward the client. Only hops
    # inside the configured trusted proxy ranges are discarded; the first
    # untrusted hop is the client identity. This prevents an attacker from
    # prepending a spoofed address to an otherwise valid forwarded chain.
    for address in reversed(addresses):
        if any(address in network for network in trusted):
            continue
        return str(address)
    return str(peer_address)


class AuthRateLimiter:
    """Atomic fixed-window limiter backed by the existing async Redis pool."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    @staticmethod
    def _key(bucket: Bucket) -> str:
        return f"{KEY_PREFIX}{bucket.name}:{digest_identifier(bucket.value)}"

    def snapshot_for_count(self, bucket: Bucket, count: int) -> BucketSnapshot:
        return BucketSnapshot(key=self._key(bucket), count=count)

    async def reserve(self, bucket: Bucket) -> int:
        key = self._key(bucket)
        try:
            result = await cast(Any, self._redis).eval(
                _INCREMENT_SCRIPT,
                1,
                key,
                str(bucket.limit),
                str(bucket.window_seconds),
            )
        except Exception as exc:
            raise RateLimitBackendUnavailableError from exc
        count, ttl = int(result[0]), int(result[1])
        if count > bucket.limit:
            logger.info(
                "auth_rate_limited",
                bucket=bucket.name,
                value_digest=digest_identifier(bucket.value),
                retry_after_seconds=max(ttl, 1),
            )
            raise RateLimitExceededError(max(ttl, 1))
        return count

    async def snapshot(self, bucket: Bucket) -> BucketSnapshot:
        key = self._key(bucket)
        try:
            raw = await cast(Any, self._redis).get(key)
        except Exception as exc:
            raise RateLimitBackendUnavailableError from exc
        return BucketSnapshot(key=key, count=int(raw or 0))

    async def clear_if_unchanged(self, snapshot: BucketSnapshot) -> None:
        try:
            await cast(Any, self._redis).eval(
                _CLEAR_IF_UNCHANGED_SCRIPT,
                1,
                snapshot.key,
                str(snapshot.count),
            )
        except Exception as exc:
            raise RateLimitBackendUnavailableError from exc

    async def record_failure(self, bucket: Bucket) -> None:
        await self.reserve(bucket)


async def enforce(
    request: Request,
    settings: Settings,
    redis: aioredis.Redis | None,
    buckets: list[Bucket],
    *,
    fail_open: bool = False,
) -> AuthRateLimiter | None:
    if redis is None:
        logger.warning(
            "auth_rate_limiter_unavailable",
            endpoint=request.url.path,
            failure_mode="open" if fail_open else "closed",
        )
        if fail_open:
            return None
        raise RateLimitBackendUnavailableError
    limiter = AuthRateLimiter(redis)
    try:
        for bucket in buckets:
            await limiter.reserve(bucket)
    except RateLimitBackendUnavailableError:
        logger.warning(
            "auth_rate_limiter_unavailable",
            endpoint=request.url.path,
            failure_mode="open" if fail_open else "closed",
        )
        if fail_open:
            return limiter
        raise
    return limiter


def ip_bucket(
    request: Request, settings: Settings, name: str, limit: int, window: int
) -> Bucket:
    return Bucket(
        name=f"{name}:ip",
        value=client_ip(request, settings),
        limit=limit,
        window_seconds=window,
    )


def identifier_bucket(name: str, value: str, limit: int, window: int) -> Bucket:
    return Bucket(name=name, value=value, limit=limit, window_seconds=window)
