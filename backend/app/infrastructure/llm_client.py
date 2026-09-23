# ruff: noqa: E501
"""Centralized LLM client with multi-provider fallback.

Ported from hr-ai-nterviewer-saas (``services/llm_client.py``) and adapted to
this project's ``Settings`` + structlog conventions. Supports OpenAI, DeepSeek,
and Anthropic via the OpenAI-compatible SDK. On retryable failures (timeouts,
connection errors, rate limits, 5xx) we fall back to the next configured
provider. Programmer errors (4xx other than 429) propagate immediately.
"""

from __future__ import annotations

from typing import Any, Literal

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    RateLimitError,
)
from openai.types.chat import ChatCompletion

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("llm_client")

_TIMEOUT = 60.0
_MAX_RETRIES = 2

# Providers that honour ``response_format={"type": "json_object"}``.
_JSON_MODE_PROVIDERS = frozenset({"openai", "deepseek"})

ProviderFailureCategory = Literal[
    "no_provider_config",
    "timeout_or_connection",
    "rate_limit",
    "server_error",
    "auth_error",
    "other_http_error",
    "unknown_provider_error",
]


class LLMUnavailableError(RuntimeError):
    """Raised when no LLM provider is configured or all providers failed."""

    def __init__(
        self,
        message: str = "LLM service unavailable",
        *,
        provider_failure_category: ProviderFailureCategory = "unknown_provider_error",
        providers_exhausted: bool = False,
        attempted_provider_names: tuple[str, ...] = (),
        attempt_categories: tuple[ProviderFailureCategory, ...] = (),
    ) -> None:
        super().__init__(message)
        self.provider_failure_category = provider_failure_category
        self.providers_exhausted = providers_exhausted
        self.attempted_provider_names = attempted_provider_names
        self.attempt_categories = attempt_categories
        self.attempt_count = len(attempted_provider_names)


def _build_client(provider_cfg: dict[str, str | None], *, timeout: float | None = None) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=provider_cfg["api_key"],
        base_url=provider_cfg.get("base_url"),
        timeout=timeout or _TIMEOUT,
        max_retries=_MAX_RETRIES,
    )


async def llm_chat_completion(
    *,
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.4,
    json_mode: bool = False,
    openai_only: bool = False,
    timeout: float | None = None,
    **kwargs: Any,
) -> tuple[ChatCompletion, str]:
    """Call chat completions with automatic provider fallback.

    Returns ``(response, model_name)`` where ``model_name`` is the model
    actually used (useful for cost tracking when a fallback fires).

    Fallback policy:
      * Timeout / connection / rate-limit / 5xx -> try next provider.
      * BadRequest / other 4xx -> raise (our request is malformed).
    """
    settings = get_settings()
    chain = settings.get_fallback_chain()
    if openai_only:
        chain = [provider for provider in chain if provider.get("name") == "openai"]
    if not chain:
        raise LLMUnavailableError(
            "OpenAI is required for strict source-reference verification. "
            "Set OPENAI_API_KEY."
            if openai_only
            else "No LLM provider configured. Set OPENAI_API_KEY, DEEPSEEK_API_KEY, "
            "or ANTHROPIC_API_KEY.",
            provider_failure_category="no_provider_config",
        )

    attempted_provider_names: list[str] = []
    attempt_categories: list[ProviderFailureCategory] = []

    for index, provider_cfg in enumerate(chain):
        provider_model = model or provider_cfg["model"] or settings.llm_model
        client = _build_client(provider_cfg, timeout=timeout)
        provider_name = _safe_provider_name(provider_cfg.get("name"))

        call_kwargs = dict(kwargs)
        if json_mode and provider_cfg.get("name") in _JSON_MODE_PROVIDERS:
            call_kwargs.setdefault("response_format", {"type": "json_object"})

        try:
            response = await client.chat.completions.create(
                model=provider_model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                **call_kwargs,
            )
            if index > 0:
                logger.info(
                    "llm_fallback_succeeded",
                    provider=provider_cfg.get("name"),
                    model=provider_model,
                )
            return response, provider_model

        except (APITimeoutError, APIConnectionError, RateLimitError, APIStatusError) as exc:
            category = classify_provider_failure(exc)
            if category not in {"timeout_or_connection", "rate_limit", "server_error"}:
                raise
            attempted_provider_names.append(provider_name)
            attempt_categories.append(category)
            fallbacks_remaining = len(chain) - index - 1
            logger.warning(
                "llm_provider_failed",
                provider=provider_name,
                model=provider_model,
                provider_failure_category=category,
                status_code=_safe_status_code(exc),
                fallbacks_remaining=fallbacks_remaining,
            )
            if fallbacks_remaining:
                continue
            raise LLMUnavailableError(
                "LLM service unavailable",
                provider_failure_category=category,
                providers_exhausted=True,
                attempted_provider_names=tuple(attempted_provider_names),
                attempt_categories=tuple(attempt_categories),
            ) from exc
        finally:
            await client.close()

    raise LLMUnavailableError(
        "LLM service unavailable",
        providers_exhausted=True,
        attempted_provider_names=tuple(attempted_provider_names),
        attempt_categories=tuple(attempt_categories),
    )


def _safe_provider_name(name: object) -> str:
    """Return only a known provider identifier for diagnostic metadata."""
    return name if name in {"openai", "deepseek", "anthropic"} else "unknown_provider"


def classify_provider_failure(exc: BaseException) -> ProviderFailureCategory:
    """Map known provider exceptions to fixed, non-sensitive categories."""
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return "timeout_or_connection"
    if isinstance(exc, RateLimitError):
        return "rate_limit"
    if isinstance(exc, APIStatusError):
        status_code = _safe_status_code(exc) or 0
        if status_code >= 500:
            return "server_error"
        if status_code in {401, 403} or isinstance(exc, AuthenticationError):
            return "auth_error"
        return "other_http_error"
    return "unknown_provider_error"


def _safe_status_code(exc: BaseException) -> int | None:
    """Return a numeric status only; never render an SDK exception or body."""
    status_code = getattr(exc, "status_code", None)
    return status_code if isinstance(status_code, int) else None
