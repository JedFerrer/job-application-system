"""Safe source execution and Redis-backed per-source admission.

Fetches never receive a database session.  Bounded adapters may overlap; an
adapter without a provider-enforced request bound (currently JobSpy) remains
serial so cancelling the awaiting task can never be mistaken for cancellation
of its underlying network call.
"""
# ruff: noqa: E501

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from anyio import to_thread

from app.infrastructure.job_sources.base import (
    JobQuery,
    JobSource,
    SourceSearchResult,
    fetch_compat_result,
)

_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end return 0"
_RENEW = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) end return 0"


@dataclass(frozen=True)
class SourceAdmission:
    source: str
    key: str
    token: str


class SourceAdmissionController:
    def __init__(self, redis: Any, *, lease_seconds: int = 1860, wait_seconds: float = 30.0, poll_seconds: float = 0.1) -> None:
        self._redis = redis
        self._lease_seconds = lease_seconds
        self._wait_seconds = wait_seconds
        self._poll_seconds = poll_seconds

    async def acquire(self, source: str) -> SourceAdmission | None:
        key = f"lock:job-source:{source.casefold()}"
        token = uuid.uuid4().hex
        deadline = asyncio.get_running_loop().time() + self._wait_seconds
        while True:
            if await self._redis.set(key, token, nx=True, ex=self._lease_seconds):
                return SourceAdmission(source=source, key=key, token=token)
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(self._poll_seconds)

    async def renew(self, admission: SourceAdmission) -> bool:
        result = await self._redis.eval(_RENEW, 1, admission.key, admission.token, self._lease_seconds)
        return bool(result)

    async def release(self, admission: SourceAdmission) -> bool:
        result = await self._redis.eval(_RELEASE, 1, admission.key, admission.token)
        return bool(result)


async def _search(source: JobSource, query: JobQuery) -> SourceSearchResult:
    search = getattr(source, "search", None)
    if callable(search):
        return await to_thread.run_sync(search, query)
    return await to_thread.run_sync(fetch_compat_result, source, query)


async def execute_sources(
    sources: list[JobSource], query: JobQuery, *, controller: SourceAdmissionController | None,
    concurrency: int,
) -> list[SourceSearchResult]:
    """Return results in requested order, overlapping only bounded adapters."""
    semaphore = asyncio.Semaphore(concurrency)

    async def one(source: JobSource) -> SourceSearchResult:
        admission: SourceAdmission | None = None
        renewer: asyncio.Task[None] | None = None
        if controller is not None:
            admission = await controller.acquire(source.name)
            if admission is None:
                return SourceSearchResult(source=source.name, status="unavailable", reason_code="source_busy")
        try:
            if admission is not None:
                async def renew_loop() -> None:
                    while True:
                        await asyncio.sleep(300)
                        if not await controller.renew(admission):  # type: ignore[union-attr]
                            return
                renewer = asyncio.create_task(renew_loop())
            capabilities = getattr(source, "capabilities", None)
            if capabilities is None or not capabilities.bounded_execution:
                # Lock ordering preserves serial JobSpy execution in a run.
                async with semaphore:
                    return await _search(source, query)
            async with semaphore:
                return await _search(source, query)
        except Exception as exc:  # noqa: BLE001
            reason: Literal["dependency_missing", "provider_error"] = "dependency_missing" if exc.__class__.__name__ == "JobSpyUnavailableError" else "provider_error"
            return SourceSearchResult(source=source.name, status="unavailable" if reason == "dependency_missing" else "failed", reason_code=reason)
        finally:
            if renewer is not None:
                renewer.cancel()
                try:
                    await renewer
                except asyncio.CancelledError:
                    pass
            if admission is not None:
                assert controller is not None
                await controller.release(admission)

    # JobSpy must not be scheduled alongside another JobSpy.  The coordinator
    # remains ordered; capable sources may nevertheless overlap each other.
    results: list[SourceSearchResult] = []
    pending: list[asyncio.Task[SourceSearchResult]] = []
    for source in sources:
        capabilities = getattr(source, "capabilities", None)
        if capabilities is not None and capabilities.bounded_execution:
            pending.append(asyncio.create_task(one(source)))
        else:
            if pending:
                results.extend(await asyncio.gather(*pending))
                pending = []
            results.append(await one(source))
    if pending:
        results.extend(await asyncio.gather(*pending))
    return results
