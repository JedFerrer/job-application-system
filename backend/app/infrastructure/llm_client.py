"""Centralized LLM client with multi-provider fallback.

Ported from hr-ai-nterviewer-saas (``services/llm_client.py``) and adapted to
this project's ``Settings`` + structlog conventions. Supports OpenAI, DeepSeek,
and Anthropic via the OpenAI-compatible SDK. On retryable failures (timeouts,
connection errors, rate limits, 5xx) we fall back to the next configured
provider. Programmer errors (4xx other than 429) propagate immediately.
"""

from __future__ import annotations

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

# Providers that honour ``response_format={"type": "json_object"}``.
_JSON_MODE_PROVIDERS = frozenset({"openai", "deepseek"})


class LLMUnavailableError(RuntimeError):
    """Raised when no LLM provider is configured or all providers failed."""


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
