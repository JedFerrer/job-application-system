"""Unit tests for safe LLM provider failure diagnostics with fake SDK clients."""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)
from openai.types.chat import ChatCompletion
from pytest import MonkeyPatch

from app.infrastructure import llm_client

pytestmark = pytest.mark.unit


class _Settings:
    llm_model = "default-model"

    def __init__(self, chain: list[dict[str, str | None]]) -> None:
        self._chain = chain

    def get_fallback_chain(self) -> list[dict[str, str | None]]:
        return self._chain


class _FakeClient:
    def __init__(self, outcome: object) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._outcome = outcome
        self.closed = False

    async def _create(self, **_kwargs: object) -> object:
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome

    async def close(self) -> None:
        self.closed = True


class _DiagnosticLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://provider.invalid/v1/chat/completions")


def _status_error(status: int, cls: type[APIStatusError] = APIStatusError) -> APIStatusError:
    response = httpx.Response(status, request=_request())
    return cls(
        "SECRET_PROVIDER_BODY_SENTINEL",
        response=response,
        body={"detail": "SECRET_AUTH_DETAIL_SENTINEL"},
    )


def _install_fake_clients(
    monkeypatch: MonkeyPatch, *, chain: list[dict[str, str | None]], outcomes: Sequence[object],
) -> list[_FakeClient]:
    clients: list[_FakeClient] = []
    monkeypatch.setattr(llm_client, "get_settings", lambda: _Settings(chain))

    def build_client(
        _provider: dict[str, str | None], *, timeout: float | None = None,
    ) -> _FakeClient:
        del timeout
        client = _FakeClient(outcomes[len(clients)])
        clients.append(client)
        return client

    monkeypatch.setattr(llm_client, "_build_client", build_client)
    return clients


async def _retryable_failure(
    monkeypatch: MonkeyPatch, outcome: BaseException,
) -> llm_client.LLMUnavailableError:
    _install_fake_clients(
        monkeypatch,
        chain=[{"name": "openai", "api_key": "fake", "model": "model", "base_url": None}],
        outcomes=[outcome],
    )
    with pytest.raises(llm_client.LLMUnavailableError) as exc_info:
        await llm_client.llm_chat_completion(messages=[{"role": "user", "content": "safe"}])
    return exc_info.value


@pytest.mark.asyncio
async def test_no_provider_config_is_safely_classified(monkeypatch: MonkeyPatch) -> None:
    _install_fake_clients(monkeypatch, chain=[], outcomes=[])

    with pytest.raises(llm_client.LLMUnavailableError) as exc_info:
        await llm_client.llm_chat_completion(messages=[])

    exc = exc_info.value
    assert exc.provider_failure_category == "no_provider_config"
    assert exc.providers_exhausted is False
    assert exc.attempt_count == 0


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (APITimeoutError(request=_request()), "timeout_or_connection"),
        (
            APIConnectionError(message="SECRET_REQUEST_SENTINEL", request=_request()),
            "timeout_or_connection",
        ),
        (_status_error(429, RateLimitError), "rate_limit"),
        (_status_error(503), "server_error"),
        (_status_error(401, AuthenticationError), "auth_error"),
        (_status_error(400, BadRequestError), "other_http_error"),
        (RuntimeError("SECRET_PROVIDER_BODY_SENTINEL"), "unknown_provider_error"),
    ],
)
def test_classify_provider_failure_uses_safe_fixed_categories(
    outcome: BaseException, expected: llm_client.ProviderFailureCategory,
) -> None:
    assert llm_client.classify_provider_failure(outcome) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (APITimeoutError(request=_request()), "timeout_or_connection"),
        (_status_error(429, RateLimitError), "rate_limit"),
        (_status_error(503), "server_error"),
    ],
)
async def test_retryable_provider_failures_are_wrapped_with_safe_metadata(
    monkeypatch: MonkeyPatch, outcome: BaseException, expected: llm_client.ProviderFailureCategory,
) -> None:
    exc = await _retryable_failure(monkeypatch, outcome)

    assert exc.provider_failure_category == expected
    assert exc.attempted_provider_names == ("openai",)
    assert exc.attempt_categories == (expected,)
    assert exc.attempt_count == 1
    assert "SECRET_" not in repr(exc.__dict__)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [_status_error(401, AuthenticationError), _status_error(400, BadRequestError)],
)
async def test_non_retryable_http_errors_propagate_without_fallback(
    monkeypatch: MonkeyPatch, outcome: APIStatusError,
) -> None:
    clients = _install_fake_clients(
        monkeypatch,
        chain=[
            {"name": "openai", "api_key": "fake", "model": "primary", "base_url": None},
            {"name": "deepseek", "api_key": "fake", "model": "fallback", "base_url": None},
        ],
        outcomes=[outcome, cast(ChatCompletion, SimpleNamespace(choices=[]))],
    )

    with pytest.raises(type(outcome)) as exc_info:
        await llm_client.llm_chat_completion(messages=[])

    assert exc_info.value is outcome
    assert len(clients) == 1
    assert clients[0].closed is True


@pytest.mark.asyncio
async def test_unknown_provider_exceptions_propagate_without_fallback(
    monkeypatch: MonkeyPatch,
) -> None:
    outcome = RuntimeError("SECRET_PROVIDER_BODY_SENTINEL")
    clients = _install_fake_clients(
        monkeypatch,
        chain=[
            {"name": "openai", "api_key": "fake", "model": "primary", "base_url": None},
            {"name": "deepseek", "api_key": "fake", "model": "fallback", "base_url": None},
        ],
        outcomes=[outcome, cast(ChatCompletion, SimpleNamespace(choices=[]))],
    )

    with pytest.raises(RuntimeError) as exc_info:
        await llm_client.llm_chat_completion(messages=[])

    assert exc_info.value is outcome
    assert len(clients) == 1
    assert clients[0].closed is True


@pytest.mark.asyncio
async def test_retryable_primary_failure_falls_back_without_changing_order(
    monkeypatch: MonkeyPatch,
) -> None:
    response = cast(ChatCompletion, SimpleNamespace(choices=[]))
    clients = _install_fake_clients(
        monkeypatch,
        chain=[
            {"name": "openai", "api_key": "fake", "model": "primary", "base_url": None},
            {"name": "deepseek", "api_key": "fake", "model": "fallback", "base_url": None},
        ],
        outcomes=[_status_error(503), response],
    )

    actual, model = await llm_client.llm_chat_completion(messages=[])

    assert actual is response
    assert model == "fallback"
    assert len(clients) == 2
    assert all(client.closed for client in clients)


@pytest.mark.asyncio
async def test_timeout_primary_failure_falls_back_to_a_successful_deepseek_response(
    monkeypatch: MonkeyPatch,
) -> None:
    response = cast(ChatCompletion, SimpleNamespace(choices=[]))
    clients = _install_fake_clients(
        monkeypatch,
        chain=[
            {"name": "openai", "api_key": "fake", "model": "primary", "base_url": None},
            {"name": "deepseek", "api_key": "fake", "model": "fallback", "base_url": None},
        ],
        outcomes=[APITimeoutError(request=_request()), response],
    )

    actual, model = await llm_client.llm_chat_completion(messages=[])

    assert actual is response
    assert model == "fallback"
    assert len(clients) == 2
    assert all(client.closed for client in clients)


@pytest.mark.asyncio
async def test_rate_limit_primary_failure_falls_back_to_a_successful_deepseek_response(
    monkeypatch: MonkeyPatch,
) -> None:
    response = cast(ChatCompletion, SimpleNamespace(choices=[]))
    clients = _install_fake_clients(
        monkeypatch,
        chain=[
            {"name": "openai", "api_key": "fake", "model": "primary", "base_url": None},
            {"name": "deepseek", "api_key": "fake", "model": "fallback", "base_url": None},
        ],
        outcomes=[_status_error(429, RateLimitError), response],
    )

    actual, model = await llm_client.llm_chat_completion(messages=[])

    assert actual is response
    assert model == "fallback"
    assert len(clients) == 2
    assert all(client.closed for client in clients)


@pytest.mark.asyncio
async def test_all_retryable_providers_exhausted_preserves_safe_attempt_metadata(
    monkeypatch: MonkeyPatch,
) -> None:
    _install_fake_clients(
        monkeypatch,
        chain=[
            {"name": "openai", "api_key": "fake", "model": "primary", "base_url": None},
            {"name": "deepseek", "api_key": "fake", "model": "fallback", "base_url": None},
        ],
        outcomes=[_status_error(429, RateLimitError), _status_error(503)],
    )

    with pytest.raises(llm_client.LLMUnavailableError) as exc_info:
        await llm_client.llm_chat_completion(messages=[])

    exc = exc_info.value
    assert exc.providers_exhausted is True
    assert exc.attempt_count == 2
    assert exc.attempted_provider_names == ("openai", "deepseek")
    assert exc.attempt_categories == ("rate_limit", "server_error")


@pytest.mark.asyncio
async def test_provider_failure_log_omits_sdk_exception_details(monkeypatch: MonkeyPatch) -> None:
    diagnostic_logger = _DiagnosticLogger()
    monkeypatch.setattr(llm_client, "logger", diagnostic_logger)

    await _retryable_failure(monkeypatch, _status_error(429, RateLimitError))

    assert diagnostic_logger.events == [
        (
            "llm_provider_failed",
            {
                "provider": "openai",
                "model": "model",
                "provider_failure_category": "rate_limit",
                "status_code": 429,
                "fallbacks_remaining": 0,
            },
        )
    ]
    assert "SECRET_" not in str(diagnostic_logger.events)
