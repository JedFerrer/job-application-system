"""Endpoint wiring checks for the approved authentication abuse policies."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from app.api.deps import (
    get_app_settings,
    get_auth_service,
    get_current_web_user,
    get_extension_auth_service,
    get_optional_redis,
    get_password_reset_email_provider,
)
from app.api.v1 import auth as auth_routes
from app.api.v1.extension import auth as extension_auth_routes
from app.core import rate_limit as rate_limit_module
from app.core.lifespan import register_exception_handlers
from app.core.rate_limit import (
    BucketSnapshot,
    RateLimitBackendUnavailableError,
    RateLimitExceededError,
)
from app.domain.auth.exceptions import InvalidTokenError
from app.domain.auth.schemas import (
    ForgotPasswordRequest,
    GoogleAuthRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
)
from app.domain.auth.service import IssuedTokens
from app.domain.extension.exceptions import ExtensionSessionRevokedError
from app.domain.extension.schemas import (
    ExtensionPairingCompleteRequest,
    ExtensionRefreshResponse,
)
from app.infrastructure.database import get_db
from app.models.user import User
from tests.unit.test_auth_rate_limit import _request, _settings

pytestmark = pytest.mark.unit


def _api_app(
    *,
    auth_service: Any | None = None,
    extension_service: Any | None = None,
) -> tuple[FastAPI, Any, Any, Any]:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_routes.router, prefix="/api/v1")
    app.include_router(extension_auth_routes.router, prefix="/api/v1")

    auth_service = auth_service or SimpleNamespace()
    extension_service = extension_service or SimpleNamespace()
    db = SimpleNamespace(get=AsyncMock(return_value=None), commit=AsyncMock())
    user = User(id=7, email="user@example.com", name="User", is_active=True)

    def api_settings() -> Any:
        return _settings()

    async def db_override() -> Any:
        yield db

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_app_settings] = api_settings
    app.dependency_overrides[get_optional_redis] = lambda: object()
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_extension_auth_service] = lambda: extension_service
    app.dependency_overrides[get_current_web_user] = lambda: user
    app.dependency_overrides[get_password_reset_email_provider] = lambda: object()
    return app, auth_service, extension_service, db


def _auth_body(path: str) -> dict[str, str]:
    return {
        "/api/v1/auth/register": {
            "email": "User@Example.COM",
            "name": "User",
            "password": "A-valid-password-1",
        },
        "/api/v1/auth/login": {
            "email": "User@Example.COM",
            "password": "A-valid-password-1",
        },
        "/api/v1/auth/google": {"id_token": "google-id-token"},
        "/api/v1/auth/forgot-password": {"email": "User@Example.COM"},
        "/api/v1/auth/reset-password": {
            "token": "reset-token-value",
            "password": "A-valid-password-1",
        },
        "/api/v1/extension/auth/complete": {"pairing_code": "pairing-code-value"},
        "/api/v1/extension/auth/start": {},
    }[path]


def _calls(path: str) -> dict[str, list[ast.Call]]:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    result: dict[str, list[ast.Call]] = {}
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            result[node.name] = [
                item
                for item in ast.walk(node)
                if isinstance(item, ast.Call)
                and isinstance(item.func, ast.Name)
                and item.func.id == "enforce"
            ]
    return result


def _bucket_literals(call: ast.Call) -> list[tuple[int, int]]:
    buckets = cast(Any, call.args[3])
    values: list[tuple[int, int]] = []
    for item in buckets.elts:
        assert isinstance(item, ast.Call)
        values.append((ast.literal_eval(item.args[-2]), ast.literal_eval(item.args[-1])))
    return values


def test_web_auth_endpoints_use_approved_buckets_and_fail_open_only_refresh() -> None:
    calls = _calls("app/api/v1/auth.py")
    expected = {
        "register": [(10, 3600), (3, 3600)],
        "login": [(20, 600)],
        "google_login": [(10, 60)],
        "refresh": [(30, 60)],
        "forgot_password": [(20, 3600), (3, 900)],
        "reset_password": [(10, 600), (5, 900)],
    }
    for endpoint, policy in expected.items():
        assert len(calls[endpoint]) == 1
        assert _bucket_literals(calls[endpoint][0]) == policy
        fail_open = any(
            keyword.arg == "fail_open" and ast.literal_eval(keyword.value)
            for keyword in calls[endpoint][0].keywords
        )
        assert fail_open is (endpoint == "refresh")
    login_source = Path("app/api/v1/auth.py").read_text(encoding="utf-8")
    assert (
        'identifier_bucket("login:email", normalize_email(payload.email), 5, 900)'
        in login_source
    )


def test_extension_auth_endpoints_use_approved_buckets_and_fail_open_only_refresh() -> None:
    calls = _calls("app/api/v1/extension/auth.py")
    expected = {
        "start_pairing": [(5, 3600)],
        "complete_pairing": [(10, 600), (5, 900)],
        "refresh_extension_token": [(30, 60)],
    }
    for endpoint, policy in expected.items():
        assert len(calls[endpoint]) == 1
        assert _bucket_literals(calls[endpoint][0]) == policy
        fail_open = any(
            keyword.arg == "fail_open" and ast.literal_eval(keyword.value)
            for keyword in calls[endpoint][0].keywords
        )
        assert fail_open is (endpoint == "refresh_extension_token")


async def test_web_auth_routes_fail_closed_before_service_work_when_redis_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(*args: Any, **kwargs: Any) -> None:
        raise RateLimitBackendUnavailableError

    monkeypatch.setattr(auth_routes, "enforce", unavailable)
    request = _request()
    settings = _settings()
    attempts = [
        auth_routes.register(
            request,
            Response(),
            RegisterRequest(
                email="person@example.com",
                name="Person",
                password="A-valid-password-1",
            ),
            cast(Any, None),
            settings,
            cast(Any, None),
        ),
        auth_routes.login(
            request,
            Response(),
            LoginRequest(email="person@example.com", password="A-valid-password-1"),
            cast(Any, None),
            settings,
            cast(Any, None),
        ),
        auth_routes.google_login(
            request,
            Response(),
            GoogleAuthRequest(id_token="not-validated-because-limiter-fails-first"),
            cast(Any, None),
            settings,
            cast(Any, None),
        ),
        auth_routes.forgot_password(
            request,
            ForgotPasswordRequest(email="person@example.com"),
            cast(Any, None),
            cast(Any, None),
            settings,
            cast(Any, None),
        ),
        auth_routes.reset_password(
            request,
            ResetPasswordRequest(token="reset-token", password="A-valid-password-1"),
            cast(Any, None),
            settings,
            cast(Any, None),
        ),
    ]
    for attempt in attempts:
        with pytest.raises(RateLimitBackendUnavailableError):
            await attempt


async def test_extension_pairing_routes_fail_closed_before_service_work_when_redis_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(*args: Any, **kwargs: Any) -> None:
        raise RateLimitBackendUnavailableError

    monkeypatch.setattr(extension_auth_routes, "enforce", unavailable)
    request = _request()
    settings = _settings()
    with pytest.raises(RateLimitBackendUnavailableError):
        await extension_auth_routes.start_pairing(
            request,
            cast(Any, type("Principal", (), {"id": "user-123"})()),
            cast(Any, None),
            settings,
            cast(Any, None),
        )
    with pytest.raises(RateLimitBackendUnavailableError):
        await extension_auth_routes.complete_pairing(
            request,
            ExtensionPairingCompleteRequest(pairing_code="pairing-code"),
            cast(Any, None),
            cast(Any, None),
            settings,
            cast(Any, None),
        )


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/google",
        "/api/v1/auth/forgot-password",
        "/api/v1/auth/reset-password",
        "/api/v1/extension/auth/start",
        "/api/v1/extension/auth/complete",
    ],
)
def test_registered_routes_return_the_throttle_contract_over_http(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    app, auth_service, extension_service, _db = _api_app()
    captured: list[list[Any]] = []

    async def reject(
        _request: Any,
        _settings: Any,
        _redis: Any,
        buckets: list[Any],
        **kwargs: Any,
    ) -> None:
        captured.append(buckets)
        raise RateLimitExceededError(23)

    if path.startswith("/api/v1/extension/"):
        monkeypatch.setattr(extension_auth_routes, "enforce", reject)
    else:
        monkeypatch.setattr(auth_routes, "enforce", reject)
    response = TestClient(app).post(path, json=_auth_body(path))
    assert response.status_code == 429
    assert response.json() == {
        "detail": "Too many authentication attempts. Please try again later."
    }
    assert response.headers["Retry-After"] == "23"
    assert response.headers["Cache-Control"] == "no-store"
    assert captured and captured[0]
    expected = {
        "/api/v1/auth/register": [("register:ip", 10, 3600), ("register:email", 3, 3600)],
        "/api/v1/auth/login": [("login:ip", 20, 600)],
        "/api/v1/auth/google": [("google:ip", 10, 60)],
        "/api/v1/auth/forgot-password": [
            ("forgot-password:ip", 20, 3600),
            ("forgot-password:email", 3, 900),
        ],
        "/api/v1/auth/reset-password": [
            ("reset-password:ip", 10, 600),
            ("reset-password:token", 5, 900),
        ],
        "/api/v1/extension/auth/start": [("pairing-start:user", 5, 3600)],
        "/api/v1/extension/auth/complete": [
            ("pairing-complete:ip", 10, 600),
            ("pairing-complete:code", 5, 900),
        ],
    }
    assert [
        (bucket.name, bucket.limit, bucket.window_seconds) for bucket in captured[0]
    ] == expected[path]
    assert auth_service.__dict__ == {}
    assert extension_service.__dict__ == {}


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/google",
        "/api/v1/auth/forgot-password",
        "/api/v1/auth/reset-password",
        "/api/v1/extension/auth/start",
        "/api/v1/extension/auth/complete",
    ],
)
def test_registered_fail_closed_routes_return_generic_503_before_services(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    auth_service = SimpleNamespace()
    extension_service = SimpleNamespace()
    app, _auth, _extension, _db = _api_app(
        auth_service=auth_service,
        extension_service=extension_service,
    )

    async def unavailable(*_args: Any, **_kwargs: Any) -> None:
        raise RateLimitBackendUnavailableError

    if path.startswith("/api/v1/extension/"):
        monkeypatch.setattr(extension_auth_routes, "enforce", unavailable)
    else:
        monkeypatch.setattr(auth_routes, "enforce", unavailable)
    response = TestClient(app).post(path, json=_auth_body(path))
    assert response.status_code == 503
    assert response.json() == {
        "detail": "Authentication is temporarily unavailable. Please try again later."
    }
    assert response.headers["Cache-Control"] == "no-store"
    assert "redis" not in response.text.lower()
    assert auth_service.__dict__ == {}
    assert extension_service.__dict__ == {}


def test_login_http_path_normalizes_email_and_clears_only_identifier_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(
        id=7,
        email="user@example.com",
        name="User",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    service = SimpleNamespace(
        authenticate=AsyncMock(
            return_value=IssuedTokens(
                access_token="access",
                refresh_token="refresh",
                expires_in=900,
                user=user,
            )
        )
    )
    app, _auth, _extension, _db = _api_app(auth_service=service)
    observed: dict[str, Any] = {"buckets": [], "cleared": []}

    class RecordingLimiter:
        async def reserve(self, bucket: Any) -> int:
            observed["buckets"].append(bucket)
            return 1

        def snapshot_for_count(self, bucket: Any, count: int) -> BucketSnapshot:
            return BucketSnapshot(key=bucket.name, count=count)

        async def clear_if_unchanged(self, snapshot: BucketSnapshot) -> None:
            observed["cleared"].append(snapshot.key)

    async def record(
        _request: Any,
        _settings: Any,
        _redis: Any,
        buckets: list[Any],
        **kwargs: Any,
    ) -> RecordingLimiter:
        observed["ip"] = buckets
        return RecordingLimiter()

    monkeypatch.setattr(auth_routes, "enforce", record)
    response = TestClient(app).post(
        "/api/v1/auth/login",
        json={"email": " User@Example.COM ", "password": "A-valid-password-1"},
    )
    assert response.status_code == 200
    assert [bucket.name for bucket in observed["ip"]] == ["login:ip"]
    assert [bucket.name for bucket in observed["buckets"]] == ["login:email"]
    assert observed["buckets"][0].value == "user@example.com"
    assert observed["cleared"] == ["login:email"]
    assert service.authenticate.await_count == 1


class FailingRedis:
    async def eval(self, *_args: Any, **_kwargs: Any) -> None:
        raise ConnectionError("redis://:secret-password@redis.internal:6379/0")


def test_web_refresh_fails_open_through_production_limiter_for_valid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(
        id=7,
        email="user@example.com",
        name="User",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    service = SimpleNamespace(
        refresh=AsyncMock(
            return_value=IssuedTokens(
                access_token="access",
                refresh_token="refresh",
                expires_in=900,
                user=user,
            )
        )
    )
    app, _auth, _extension, _db = _api_app(auth_service=service)
    app.dependency_overrides[get_optional_redis] = lambda: FailingRedis()
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        rate_limit_module.logger,
        "warning",
        lambda event, **kwargs: events.append((event, kwargs)),
    )
    response = TestClient(app).post(
        "/api/v1/auth/refresh",
        cookies={"refresh_token": "valid-refresh"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"] == "access"
    assert service.refresh.await_count == 1
    assert events[-1] == (
        "auth_rate_limiter_unavailable",
        {"endpoint": "/api/v1/auth/refresh", "failure_mode": "open"},
    )


def test_web_refresh_continues_after_limiter_failure_but_invalid_token_stays_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SimpleNamespace(refresh=AsyncMock(side_effect=InvalidTokenError))
    app, _auth, _extension, _db = _api_app(auth_service=service)

    async def fail_open(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(auth_routes, "enforce", fail_open)
    response = TestClient(app).post(
        "/api/v1/auth/refresh",
        cookies={"refresh_token": "expired-or-revoked-refresh"},
    )
    assert response.status_code == 401
    assert service.refresh.await_count == 1


def test_web_refresh_replay_style_rejection_survives_production_limiter_fail_open() -> None:
    service = SimpleNamespace(refresh=AsyncMock(side_effect=InvalidTokenError))
    app, _auth, _extension, _db = _api_app(auth_service=service)
    app.dependency_overrides[get_optional_redis] = lambda: FailingRedis()
    response = TestClient(app).post(
        "/api/v1/auth/refresh",
        cookies={"refresh_token": "replayed-refresh"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}
    assert service.refresh.await_count == 1


def test_pairing_start_still_requires_authentication() -> None:
    app, _auth, _extension, _db = _api_app()
    app.dependency_overrides.pop(get_current_web_user)
    response = TestClient(app).post("/api/v1/extension/auth/start")
    assert response.status_code == 401


def test_extension_refresh_continues_after_limiter_failure_but_revoked_token_stays_rejected(
) -> None:
    service = SimpleNamespace(refresh=AsyncMock(side_effect=ExtensionSessionRevokedError))
    app, _auth, _extension, db = _api_app(extension_service=service)
    app.dependency_overrides[get_optional_redis] = lambda: FailingRedis()
    response = TestClient(app).post(
        "/api/v1/extension/auth/refresh",
        json={"refresh_token": "revoked-extension-refresh"},
    )
    assert response.status_code == 401
    assert service.refresh.await_count == 1
    assert db.commit.await_count == 1


def test_extension_refresh_fails_open_through_production_limiter_for_valid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SimpleNamespace(
        refresh=AsyncMock(
            return_value=ExtensionRefreshResponse(
                access_token="extension-access",
                refresh_token="extension-refresh",
                expires_in=900,
            )
        )
    )
    app, _auth, _extension, _db = _api_app(extension_service=service)
    app.dependency_overrides[get_optional_redis] = lambda: FailingRedis()
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        rate_limit_module.logger,
        "warning",
        lambda event, **kwargs: events.append((event, kwargs)),
    )
    response = TestClient(app).post(
        "/api/v1/extension/auth/refresh",
        json={"refresh_token": "valid-extension-refresh"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "access_token": "extension-access",
        "refresh_token": "extension-refresh",
        "expires_in": 900,
    }
    assert service.refresh.await_count == 1
    assert events[-1] == (
        "auth_rate_limiter_unavailable",
        {"endpoint": "/api/v1/extension/auth/refresh", "failure_mode": "open"},
    )


class CountingRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def eval(self, script: str, _count: int, key: str, *args: str) -> list[int]:
        if "INCR" in script:
            current = self.values.get(key, 0) + 1
            self.values[key] = current
            ttl = 19
            if key.startswith("auth-rate-limit:login:ip:") and current > 2:
                return [20 + current, ttl]
            return [current, ttl]
        expected = int(args[0])
        if self.values.get(key) == expected:
            del self.values[key]
        return [1]


def test_registered_login_rejects_first_and_subsequent_requests_over_limit() -> None:
    user = User(
        id=7,
        email="user@example.com",
        name="User",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    service = SimpleNamespace(
        authenticate=AsyncMock(
            return_value=IssuedTokens(
                access_token="access",
                refresh_token="refresh",
                expires_in=900,
                user=user,
            )
        )
    )
    app, _auth, _extension, _db = _api_app(auth_service=service)
    counting_redis = CountingRedis()
    app.dependency_overrides[get_optional_redis] = lambda: counting_redis
    client = TestClient(app)
    responses = [
        client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "A-valid-password-1"},
        )
        for _ in range(4)
    ]
    assert [response.status_code for response in responses] == [200, 200, 429, 429]
    for response in responses[2:]:
        assert response.headers["Retry-After"] == "19"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json() == {
            "detail": "Too many authentication attempts. Please try again later."
        }
    assert service.authenticate.await_count == 2
