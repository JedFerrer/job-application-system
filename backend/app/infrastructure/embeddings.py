"""OpenAI embeddings client used for job/profile matching (Phase 3).

Wrapped with tenacity retries per the backend resilience conventions.
"""

from __future__ import annotations

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("embeddings")

_RETRYABLE = (APITimeoutError, APIConnectionError, RateLimitError)


def _client() -> AsyncOpenAI:
    settings = get_settings()
    api_key = settings.openai_api_key.get_secret_value()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for embeddings.")
    return AsyncOpenAI(api_key=api_key, timeout=30.0)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(_RETRYABLE),
    reraise=True,
)
async def embed_text(text: str) -> list[float]:
    """Return the embedding vector for a single text input."""
    settings = get_settings()
    response = await _client().embeddings.create(
        model=settings.embedding_model,
        input=text,
    )
    return response.data[0].embedding


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(_RETRYABLE),
    reraise=True,
)
async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Return embedding vectors for a batch of text inputs (order preserved)."""
    if not texts:
        return []
    settings = get_settings()
    response = await _client().embeddings.create(
        model=settings.embedding_model,
        input=texts,
    )
    ordered = sorted(response.data, key=lambda item: item.index)
    return [item.embedding for item in ordered]
