"""Focused Phase C2A email-provider and cooldown regression tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from starlette.requests import Request

from app.api.v1.auth import forgot_password as forgot_password_route
from app.core.config import Settings
from app.domain.auth.service import AuthService
from app.infrastructure import email_service
from app.infrastructure.email_service import (
    EmailDeliveryError,
    MockEmailProvider,
    ResendEmailProvider,
    get_email_provider,
)

pytestmark = pytest.mark.unit


def _settings(**overrides: object) -> Settings:
    settings = Settings(
        environment="development",
        jwt_secret=SecretStr("test-secret-for-testing-12345678"),
        email_delivery_mode="resend",
        resend_api_key=SecretStr("re_test_key"),
        resend_from_email="no-reply@example.test",
        resend_from_name="Job Application System",
        password_reset_url="http://localhost:3000/reset-password",
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def test_test_environment_forces_no_network_provider() -> None:
    provider = get_email_provider(
        _settings(environment="test", email_delivery_mode="resend")
    )
    assert isinstance(provider, MockEmailProvider)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["mock", "console"])
async def test_mock_and_console_modes_never_use_network(mode: str) -> None:
    provider = get_email_provider(_settings(email_delivery_mode=mode))
    await provider.send_password_reset(
        to_email="user@example.test",
        reset_url="http://localhost:3000/reset-password?token=abc",
        user_id=7,
        reset_version=1,
    )
    assert isinstance(provider, MockEmailProvider)


def test_resend_mode_requires_api_key() -> None:
    with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
        get_email_provider(_settings(resend_api_key=SecretStr("")))


@pytest.mark.asyncio
async def test_resend_payload_and_idempotency_key(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict[str, object]] = []

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, _url: str, *, headers: dict[str, str], json: object) -> httpx.Response:
            requests.append({"headers": headers, "json": json})
            return httpx.Response(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    await ResendEmailProvider(_settings()).send_password_reset(
        to_email="user@example.test",
        reset_url="http://localhost:3000/reset-password?token=abc%2B123",
        user_id=7,
        reset_version=4,
    )

    assert len(requests) == 1
    headers = requests[0]["headers"]
    payload = requests[0]["json"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer re_test_key"
    assert headers["User-Agent"].startswith("job-application-system")
    assert headers["Idempotency-Key"] == "password-reset/7/4"
    assert "user@example.test" not in headers["Idempotency-Key"]
    assert isinstance(payload, dict)
    assert payload["to"] == ["user@example.test"]
    assert payload["subject"] == "Reset your password"
    assert "15 minutes" in payload["text"]
    assert "15 minutes" in payload["html"]
    assert "reply_to" not in payload


@pytest.mark.asyncio
async def test_resend_payload_includes_configured_reply_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[dict[str, object]] = []

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(
            self,
            *_args: object,
            json: dict[str, object],
            **_kwargs: object,
        ) -> httpx.Response:
            payloads.append(json)
            return httpx.Response(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    await ResendEmailProvider(
        _settings(resend_reply_to="security@example.test")
    ).send_password_reset(
        to_email="user@example.test",
        reset_url="http://localhost:3000/reset-password?token=abc",
        user_id=7,
        reset_version=4,
    )
    assert payloads[0]["reply_to"] == "security@example.test"


@pytest.mark.asyncio
async def test_eligible_user_gets_one_provider_operation_and_generic_response() -> None:
    repo = AsyncMock()
    repo.get_by_email.return_value = SimpleNamespace(id=7, hashed_password="hash")
    repo.advance_password_reset_version.return_value = 1
    provider = MockEmailProvider()

    result = await AuthService(repo, _settings()).forgot_password(
        email="user@example.test", email_provider=provider
    )

    assert result == {"message": "If the email is registered, a reset link has been sent."}
    assert len(provider.calls) == 1
    assert provider.calls[0]["to_email"] == "user@example.test"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ConnectError("down"), httpx.ReadTimeout("slow")])
async def test_resend_retries_network_failures(
    monkeypatch: pytest.MonkeyPatch, failure: httpx.RequestError
) -> None:
    calls = 0

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise failure

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    with pytest.raises(EmailDeliveryError):
        await ResendEmailProvider(_settings()).send_password_reset(
            to_email="user@example.test",
            reset_url="http://localhost:3000/reset-password?token=abc",
            user_id=7,
            reset_version=4,
        )
    assert calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_resend_retries_transient_http_statuses(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    calls = 0

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(status)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    with pytest.raises(EmailDeliveryError):
        await ResendEmailProvider(_settings()).send_password_reset(
            to_email="user@example.test",
            reset_url="http://localhost:3000/reset-password?token=abc",
            user_id=7,
            reset_version=4,
        )
    assert calls == 3


@pytest.mark.asyncio
async def test_retries_reuse_idempotency_key(monkeypatch: pytest.MonkeyPatch) -> None:
    headers_seen: list[dict[str, str]] = []
    calls = 0

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(
            self, _url: str, *, headers: dict[str, str], **_kwargs: object
        ) -> httpx.Response:
            nonlocal calls
            calls += 1
            headers_seen.append(headers)
            return httpx.Response(500 if calls == 1 else 200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    await ResendEmailProvider(_settings()).send_password_reset(
        to_email="user@example.test",
        reset_url="http://localhost:3000/reset-password?token=secret-token",
        user_id=7,
        reset_version=4,
    )

    assert [headers["Idempotency-Key"] for headers in headers_seen] == [
        "password-reset/7/4",
        "password-reset/7/4",
    ]


@pytest.mark.asyncio
async def test_unexpected_provider_exception_raises_safe_delivery_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> httpx.Response:
            raise ValueError("provider body includes secret-token")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    with pytest.raises(EmailDeliveryError) as caught:
        await ResendEmailProvider(_settings()).send_password_reset(
            to_email="user@example.test",
            reset_url="http://localhost:3000/reset-password?token=secret-token",
            user_id=7,
            reset_version=4,
        )
    assert str(caught.value) == "Password reset delivery failed"
    assert "secret-token" not in str(caught.value)


@pytest.mark.asyncio
async def test_route_maps_delivery_failure_to_generic_503() -> None:
    service = AsyncMock()
    service.forgot_password.side_effect = EmailDeliveryError()
    payload = SimpleNamespace(email="user@example.test")

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/forgot-password",
            "headers": [],
            "query_string": b"",
        }
    )
    with pytest.raises(HTTPException) as caught:
        await forgot_password_route(
            request=request,
            payload=payload,
            service=service,
            email_provider=MockEmailProvider(),
            redis=AsyncMock(),
        )

    response = caught.value
    assert response.status_code == 503
    assert response.detail == (
        "If the email is registered, a reset link has been sent."
    )
    assert "user@example.test" not in str(response)
    assert "secret-token" not in str(response)


@pytest.mark.asyncio
async def test_delivery_logs_are_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    records: list[tuple[object, ...]] = []

    class FakeLogger:
        def info(self, *args: object, **_kwargs: object) -> None:
            records.append(args)

        def warning(self, *args: object, **_kwargs: object) -> None:
            records.append(args)

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return httpx.Response(400, text="provider-secret-body")

    monkeypatch.setattr(email_service, "logger", FakeLogger())
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    with pytest.raises(EmailDeliveryError):
        await ResendEmailProvider(_settings()).send_password_reset(
            to_email="user@example.test",
            reset_url="http://localhost:3000/reset-password?token=secret-token",
            user_id=7,
            reset_version=4,
        )

    rendered = repr(records)
    assert "user@example.test" not in rendered
    assert "secret-token" not in rendered
    assert "provider-secret-body" not in rendered
    assert "re_test_key" not in rendered


@pytest.mark.asyncio
async def test_resend_does_not_retry_permanent_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(400)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    with pytest.raises(EmailDeliveryError):
        await ResendEmailProvider(_settings()).send_password_reset(
            to_email="user@example.test",
            reset_url="http://localhost:3000/reset-password?token=abc",
            user_id=7,
            reset_version=4,
        )
    assert calls == 1


@pytest.mark.asyncio
async def test_recipient_cooldown_hashes_email_and_prevents_second_send() -> None:
    repo = AsyncMock()
    repo.get_by_email.return_value = SimpleNamespace(id=7, hashed_password="hash")
    repo.advance_password_reset_version.return_value = 1
    redis = AsyncMock()
    redis.set.return_value = False
    provider = MockEmailProvider()
    service = AuthService(repo, _settings(),)

    result = await service.forgot_password(
        email="User@Example.test",
        email_provider=provider,
        cooldown_redis=redis,
    )

    assert result == {"message": "If the email is registered, a reset link has been sent."}
    assert provider.calls == []
    key = redis.set.await_args.args[0]
    assert "User@Example.test" not in key
    assert "@" not in key


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user",
    [
        None,
        SimpleNamespace(id=7, hashed_password="hash"),
        SimpleNamespace(id=7, hashed_password=None),
    ],
)
async def test_ineligible_users_do_not_send(
    user: object | None,
) -> None:
    repo = AsyncMock()
    repo.get_by_email.return_value = user
    repo.advance_password_reset_version.return_value = None
    provider = MockEmailProvider()
    service = AuthService(repo, _settings())

    result = await service.forgot_password(email="user@example.test", email_provider=provider)

    assert result == {"message": "If the email is registered, a reset link has been sent."}
    assert provider.calls == []


@pytest.mark.asyncio
async def test_inactive_password_user_stays_generic_without_reset_work() -> None:
    repo = AsyncMock()
    repo.get_by_email.return_value = SimpleNamespace(
        id=7, hashed_password="hash", is_active=False
    )
    provider = MockEmailProvider()
    redis = AsyncMock()

    result = await AuthService(repo, _settings()).forgot_password(
        email="inactive@example.test",
        email_provider=provider,
        cooldown_redis=redis,
    )

    assert result == {"message": "If the email is registered, a reset link has been sent."}
    assert provider.calls == []
    repo.advance_password_reset_version.assert_not_awaited()
    redis.set.assert_not_awaited()
