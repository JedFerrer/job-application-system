"""Centralized LLM client with multi-provider fallback.

Ported from hr-ai-nterviewer-saas (``services/llm_client.py``) and adapted to
this project's ``Settings`` + structlog conventions. Supports OpenAI, DeepSeek,
and Anthropic via the OpenAI-compatible SDK. On retryable failures (timeouts,
connection errors, rate limits, 5xx) we fall back to the next configured
provider. Programmer errors (4xx other than 429) propagate immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    BadRequestError,
    RateLimitError,
)
from openai.types.chat import ChatCompletion

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("llm_client")

_TIMEOUT = 60.0
_MAX_RETRIES = 2
LLM_REQUEST_POLICY_VERSION = "llm-request-policy-v1"

# Providers that honour ``response_format={"type": "json_object"}``.
_JSON_MODE_PROVIDERS = frozenset({"openai", "deepseek"})


def normalize_model_identity(value: object) -> str | None:
    """Return a nonblank model identity without inventing or rewriting it."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value


class LLMUnavailableError(RuntimeError):
    """Raised when no LLM provider is configured or all providers failed."""


@dataclass(frozen=True)
class LLMProviderRequest:
    name: str
    api_key: str
    base_url: str | None
    model: str
    response_format_mode: str

    def cache_identity(self) -> dict[str, str]:
        return {
            "name": self.name,
            "model": self.model,
            "response_format_mode": self.response_format_mode,
        }


@dataclass(frozen=True)
class LLMRequestSpec:
    model: str
    temperature: float
    json_mode: bool
    providers: tuple[LLMProviderRequest, ...]

    def cache_identity(self) -> dict[str, object]:
        return {
            "policy_version": LLM_REQUEST_POLICY_VERSION,
            "temperature": self.temperature,
            "json_mode": self.json_mode,
            "providers": [provider.cache_identity() for provider in self.providers],
        }


@dataclass(frozen=True)
class LLMCompletionMetadata:
    provider: str
    requested_model: str
    # ``None`` means the provider did not report a trustworthy serving-model
    # identity.  It must never be inferred from the requested model.
    actual_model: str | None
    fallback_occurred: bool


def resolve_llm_request_spec(
    *, model: str | None, temperature: float, json_mode: bool
) -> LLMRequestSpec | None:
    """Resolve the exact ordered provider policy before a request is hashed."""
    settings = get_settings()
    model_identity = normalize_model_identity(model)
    if model_identity is None:
        return None
    providers = tuple(
        LLMProviderRequest(
            name=str(provider_cfg["name"]),
            api_key=str(provider_cfg["api_key"]),
            base_url=provider_cfg.get("base_url"),
            model=model_identity,
            response_format_mode=(
                "provider_json_object"
                if json_mode and provider_cfg.get("name") in _JSON_MODE_PROVIDERS
                else "no_response_format"
            ),
        )
        for provider_cfg in settings.get_fallback_chain()
    )
    if not providers:
        return None
    return LLMRequestSpec(
        model=model_identity,
        temperature=temperature,
        json_mode=json_mode,
        providers=providers,
    )


def _build_client(provider_cfg: dict[str, str | None]) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=provider_cfg["api_key"],
        base_url=provider_cfg.get("base_url"),
        timeout=_TIMEOUT,
        max_retries=_MAX_RETRIES,
    )


async def llm_chat_completion(
    *,
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.4,
    json_mode: bool = False,
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
    if not chain:
        raise LLMUnavailableError(
            "No LLM provider configured. Set OPENAI_API_KEY, DEEPSEEK_API_KEY, "
            "or ANTHROPIC_API_KEY."
        )

    last_exc: Exception | None = None

    for index, provider_cfg in enumerate(chain):
        provider_model = model or provider_cfg["model"] or settings.llm_model
        client = _build_client(provider_cfg)

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

        except BadRequestError:
            raise
        except (APITimeoutError, APIConnectionError, RateLimitError) as exc:
            last_exc = exc
            logger.warning(
                "llm_provider_failed",
                provider=provider_cfg.get("name"),
                model=provider_model,
                error=str(exc),
                fallbacks_remaining=len(chain) - index - 1,
            )
            continue
        except APIStatusError as exc:
            status_code = getattr(exc, "status_code", None) or 0
            if status_code >= 500:
                last_exc = exc
                logger.warning(
                    "llm_provider_5xx",
                    provider=provider_cfg.get("name"),
                    model=provider_model,
                    status=status_code,
                    fallbacks_remaining=len(chain) - index - 1,
                )
                continue
            raise

    raise LLMUnavailableError("All LLM providers failed") from last_exc


async def llm_chat_completion_for_spec(
    *, messages: list[dict[str, str]], request_spec: LLMRequestSpec
) -> tuple[ChatCompletion, LLMCompletionMetadata]:
    """Execute exactly the pre-resolved request policy used for cache identity."""
    last_exc: Exception | None = None
    for index, provider in enumerate(request_spec.providers):
        client = AsyncOpenAI(
            api_key=provider.api_key,
            base_url=provider.base_url,
            timeout=_TIMEOUT,
            max_retries=_MAX_RETRIES,
        )
        call_kwargs: dict[str, object] = {}
        if provider.response_format_mode == "provider_json_object":
            call_kwargs["response_format"] = {"type": "json_object"}
        try:
            completion_create: Any = client.chat.completions.create
            response = await completion_create(
                model=provider.model,
                messages=messages,
                temperature=request_spec.temperature,
                **call_kwargs,
            )
            reported_model = getattr(response, "model", None)
            actual_model = normalize_model_identity(reported_model)
            metadata = LLMCompletionMetadata(
                provider=provider.name,
                requested_model=provider.model,
                actual_model=actual_model,
                fallback_occurred=index > 0,
            )
            return response, metadata
        except BadRequestError:
            raise
        except (APITimeoutError, APIConnectionError, RateLimitError) as exc:
            last_exc = exc
            continue
        except APIStatusError as exc:
            if (getattr(exc, "status_code", None) or 0) >= 500:
                last_exc = exc
                continue
            raise
    raise LLMUnavailableError("All LLM providers failed") from last_exc
