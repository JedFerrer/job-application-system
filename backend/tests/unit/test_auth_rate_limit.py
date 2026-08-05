"""Focused tests for shared authentication abuse controls."""

from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core import lifespan as lifespan_module
from app.core import rate_limit as rate_limit_module
from app.core.config import Settings
from app.core.lifespan import register_exception_handlers
from app.core.rate_limit import (
    AuthRateLimiter,
    Bucket,
    RateLimitBackendUnavailableError,
    RateLimitExceededError,
    client_ip,
    digest_identifier,
    enforce,
    ip_bucket,
)

pytestmark = pytest.mark.unit


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.fail = False
        self.scripts: list[tuple[str, str]] = []

    async def eval(self, script: str, count: int, key: str, *args: str) -> list[int]:
        if self.fail:
            raise ConnectionError("redis unavailable")
        self.scripts.append((key, script))
        if "INCR" in script:
            current = self.values.get(key, 0) + 1
            self.values[key] = current
            self.ttls.setdefault(key, int(args[1]))
            return [current, self.ttls[key]]
        expected = int(args[0])
        if self.values.get(key) == expected:
            del self.values[key]
            self.ttls.pop(key, None)
        return [1]

    async def get(self, key: str) -> int | None:
        if self.fail:
            raise ConnectionError("redis unavailable")
        return self.values.get(key)


def _request(*, peer: str = "198.51.100.10", forwarded: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": headers,
            "client": (peer, 1234),
            "query_string": b"",
        }
    )


def _settings(*cidrs: str) -> Settings:
    return Settings(
        environment="test",
        jwt_secret="test-secret-for-rate-limit-123456789",
        trusted_proxy_cidrs=list(cidrs),
    )


def test_lifespan_rate_limit_unavailable_response_logs_closed_mode_without_sensitive_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/api/v1/auth/login")
    async def trigger() -> None:
        raise RateLimitBackendUnavailableError

    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        lifespan_module.logger,
        "warning",
        lambda event, **kwargs: events.append((event, kwargs)),
    )
    response = TestClient(app).get(
        "/api/v1/auth/login?email=user@example.com",
        headers={
            "Authorization": "Bearer access-token-secret",
            "X-Forwarded-For": "203.0.113.10",
        },
    )
    assert response.status_code == 503
    assert events == [
        (
            "auth_rate_limiter_unavailable_response",
            {"endpoint": "/api/v1/auth/login", "failure_mode": "closed"},
        )
    ]
    rendered = str(events)
    for value in (
        "user@example.com",
        "access-token-secret",
        "203.0.113.10",
        "redis://:secret-password@redis.internal:6379/0",
    ):
        assert value not in rendered


async def test_shared_instances_enforce_and_clear_only_unchanged_bucket() -> None:
    redis = FakeRedis()
    bucket = Bucket("login:email", "user@example.com", 2, 900)
    first = AuthRateLimiter(cast(Any, redis))
    second = AuthRateLimiter(cast(Any, redis))

    assert await first.reserve(bucket) == 1
    assert await second.reserve(bucket) == 2
    with pytest.raises(RateLimitExceededError):
        await first.reserve(bucket)

    snapshot = first.snapshot_for_count(bucket, 2)
    await second.reserve(Bucket("login:email", "user@example.com", 5, 900))
    await first.clear_if_unchanged(snapshot)
    assert redis.values[snapshot.key] == 4


async def test_redis_failure_fails_closed_and_refresh_can_fail_open() -> None:
    redis = FakeRedis()
    redis.fail = True
    request = _request()
    bucket = Bucket("login:ip", "198.51.100.10", 20, 600)

    with pytest.raises(RateLimitBackendUnavailableError):
        await enforce(request, _settings(), cast(Any, redis), [bucket])
    await enforce(request, _settings(), cast(Any, redis), [bucket], fail_open=True)


def test_throttle_response_is_generic_and_has_retry_metadata() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/auth/login")
    async def limited() -> None:
        raise RateLimitExceededError(17)

    response = TestClient(app).get("/auth/login")
    assert response.status_code == 429
    assert response.json() == {
        "detail": "Too many authentication attempts. Please try again later."
    }
    assert response.headers["Retry-After"] == "17"
    assert response.headers["Cache-Control"] == "no-store"


def test_backend_failure_response_is_generic_and_redacts_infrastructure() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/auth/register")
    async def unavailable() -> None:
        raise RateLimitBackendUnavailableError

    response = TestClient(app).get("/auth/register")
    assert response.status_code == 503
    assert response.json() == {
        "detail": "Authentication is temporarily unavailable. Please try again later."
    }
    assert "redis" not in response.text.lower()
    assert response.headers["Cache-Control"] == "no-store"


def test_forwarded_headers_require_trusted_peer_and_malformed_values_fallback() -> None:
    request = _request(forwarded="203.0.113.8")
    assert client_ip(request, _settings()) == "198.51.100.10"
    assert (
        client_ip(_request(peer="10.0.0.2", forwarded="203.0.113.8"), _settings("10.0.0.0/8"))
        == "203.0.113.8"
    )
    assert (
        client_ip(
            _request(peer="10.0.0.2", forwarded="198.51.100.200, 198.51.100.8, 10.0.0.3"),
            _settings("10.0.0.0/8"),
        )
        == "198.51.100.8"
    )
    assert (
        client_ip(
            _request(peer="10.0.0.2", forwarded="198.51.100.8, 10.0.0.3, 10.0.0.4"),
            _settings("10.0.0.0/8"),
        )
        == "198.51.100.8"
    )
    assert (
        client_ip(
            _request(peer="10.0.0.2", forwarded="2001:db8::8, fd00::3"),
            _settings("10.0.0.0/8", "fd00::/8"),
        )
        == "2001:db8::8"
    )
    assert (
        client_ip(
            _request(peer="10.0.0.2", forwarded="10.0.0.3, 10.0.0.4"),
            _settings("10.0.0.0/8"),
        )
        == "10.0.0.2"
    )
    assert (
        client_ip(
            _request(peer="10.0.0.2", forwarded="198.51.100.8,,10.0.0.3"),
            _settings("10.0.0.0/8"),
        )
        == "10.0.0.2"
    )
    assert (
        client_ip(_request(peer="10.0.0.2", forwarded="not-an-ip"), _settings("10.0.0.0/8"))
        == "10.0.0.2"
    )
    assert (
        client_ip(
            _request(peer="10.0.0.2", forwarded="198.51.100.8, 172.16.1.10, 10.0.0.3"),
            _settings("10.0.0.0/8"),
        )
        == "172.16.1.10"
    )
    assert (
        client_ip(
            _request(peer="10.1.0.2", forwarded="203.0.113.8"), _settings("10.0.0.0/16")
        )
        == "10.1.0.2"
    )


def test_spoofed_prefix_cannot_create_a_distinct_ip_rate_limit_bucket() -> None:
    settings = _settings("10.0.0.0/8")
    safe = ip_bucket(
        _request(peer="10.0.0.2", forwarded="198.51.100.250, 203.0.113.8, 10.0.0.3"),
        settings,
        "login",
        20,
        600,
    )
    same_client = ip_bucket(
        _request(peer="10.0.0.2", forwarded="203.0.113.8, 10.0.0.3"),
        settings,
        "login",
        20,
        600,
    )
    assert safe.value == same_client.value == "203.0.113.8"
    assert AuthRateLimiter(cast(Any, FakeRedis()))._key(safe) == AuthRateLimiter(
        cast(Any, FakeRedis())
    )._key(same_client)


async def test_keys_and_digests_do_not_store_raw_identifiers() -> None:
    redis = FakeRedis()
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
    buckets = [Bucket(f"login:{i}", value, 1, 900) for i, value in enumerate(sensitive)]
    limiter = AuthRateLimiter(cast(Any, redis))
    for bucket in buckets:
        assert bucket.value not in limiter._key(bucket)
        await limiter.reserve(bucket)
    assert all(raw not in key for key in redis.values for raw in sensitive)


async def test_rate_limit_logging_contains_only_safe_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        rate_limit_module.logger,
        "info",
        lambda event, **kwargs: events.append((event, kwargs)),
    )
    redis = FakeRedis()
    sensitive = [
        "user@example.com",
        "USER@EXAMPLE.COM",
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
    limiter = AuthRateLimiter(cast(Any, redis))
    for index, value in enumerate(sensitive):
        bucket = Bucket(f"login:class:{index}", value, 1, 900)
        await limiter.reserve(bucket)
        with pytest.raises(RateLimitExceededError):
            await limiter.reserve(bucket)
    rendered = repr(events)
    assert all(value not in rendered for value in sensitive)
    assert digest_identifier("user@example.com") in rendered


async def test_backend_failure_logging_redacts_request_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        rate_limit_module.logger,
        "warning",
        lambda event, **kwargs: events.append((event, kwargs)),
    )
    request = _request(peer="203.0.113.9", forwarded="198.51.100.8")
    with pytest.raises(RateLimitBackendUnavailableError):
        await enforce(
            request,
            _settings(),
            None,
            [Bucket("login:email", "person@example.com", 1, 60)],
        )
    assert events == [
        (
            "auth_rate_limiter_unavailable",
            {"endpoint": "/api/v1/auth/login", "failure_mode": "closed"},
        )
    ]


async def test_backend_failure_logging_marks_fail_open_without_sensitive_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        rate_limit_module.logger,
        "warning",
        lambda event, **kwargs: events.append((event, kwargs)),
    )
    request = _request(
        peer="10.0.0.2",
        forwarded="198.51.100.8, 10.0.0.3",
    )
    assert (
        await enforce(
            request,
            _settings("10.0.0.0/8"),
            None,
            [
                Bucket("refresh:ip", "198.51.100.8", 30, 60),
                Bucket("refresh:token", "refresh-token", 1, 60),
            ],
            fail_open=True,
        )
        is None
    )
    assert events == [
        (
            "auth_rate_limiter_unavailable",
            {"endpoint": "/api/v1/auth/login", "failure_mode": "open"},
        )
    ]
