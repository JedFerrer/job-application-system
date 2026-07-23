"""Coordinate job-description embeddings across API and worker callers."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

import redis.asyncio as aioredis

from app.infrastructure.embeddings import embed_text

_LOCK_TTL_SECONDS = 45
_POLL_INTERVAL_SECONDS = 0.25
_POLL_ATTEMPTS = 12


@dataclass(frozen=True)
class EmbeddingResult:
    status: Literal["ready", "pending", "unavailable"]


class JobEmbeddingRepository(Protocol):
    async def get_by_id(self, job_id: int) -> Any: ...

    async def set_embedding(self, job_id: int, embedding: list[float]) -> None: ...

    async def commit(self) -> None: ...


class JobEmbeddingCoordinator:
    """Ensure one job embedding exists without duplicate provider calls."""

    def __init__(self, repository: JobEmbeddingRepository, redis: aioredis.Redis) -> None:
        self._repo = repository
        self._redis = redis

    async def ensure_embedding(self, job_id: int, *, force: bool = False) -> EmbeddingResult:
        job = await self._repo.get_by_id(job_id)
        if job is None or not (job.description or "").strip():
            return EmbeddingResult("unavailable")
        if job.description_embedding is not None and not force:
            return EmbeddingResult("ready")

        lock_key = f"job-embedding:{job_id}"
        token = uuid.uuid4().hex
        acquired = await self._redis.set(lock_key, token, nx=True, ex=_LOCK_TTL_SECONDS)
        if not acquired:
            return await self._wait_for_embedding(job_id)

        try:
            # A competing caller may have completed between the first read and
            # lock acquisition, so re-check immediately before provider work.
            job = await self._repo.get_by_id(job_id)
            if job is None or not (job.description or "").strip():
                return EmbeddingResult("unavailable")
            if job.description_embedding is None or force:
                description = job.description
                if description is None:
                    return EmbeddingResult("unavailable")
                embedding = await embed_text(description.strip())
                await self._repo.set_embedding(job_id, embedding)
                # The lock protects provider work across processes. Persist the
                # value before releasing it so a waiter can observe the result.
                await self._repo.commit()
            return EmbeddingResult("ready")
        finally:
            # Do not delete a lock that expired and was acquired by another caller.
            release = self._redis.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then "
                "return redis.call('del', KEYS[1]) end return 0",
                1,
                lock_key,
                token,
            )
            await cast(Awaitable[Any], release)

    async def _wait_for_embedding(self, job_id: int) -> EmbeddingResult:
        for _ in range(_POLL_ATTEMPTS):
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            job = await self._repo.get_by_id(job_id)
            if job is None or not (job.description or "").strip():
                return EmbeddingResult("unavailable")
            if job.description_embedding is not None:
                return EmbeddingResult("ready")
        return EmbeddingResult("pending")
