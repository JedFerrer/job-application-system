"""Deterministic Unit 1B execution/admission coverage."""

from __future__ import annotations

import asyncio
import threading

import pytest
from anyio import to_thread

from app.infrastructure.job_sources.base import (
    JobQuery,
    RawJobPost,
    SourceCapabilities,
    SourceDetailResult,
    SourceSearchResult,
)
from app.infrastructure.job_sources.execution import (
    SourceAdmissionController,
    execute_sources,
)
from app.infrastructure.job_sources.jobspy_source import JobSpySource

pytestmark = pytest.mark.unit


class Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script: str, count: int, key: str, token: str, *args: object) -> int:
        if self.values.get(key) != token:
            return 0
        if "del" in script:
            del self.values[key]
        return 1


class Source:
    def __init__(self, name: str, gate: threading.Barrier, bounded: bool = True) -> None:
        self.name, self.gate, self.bounded = name, gate, bounded

    @property
    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            source=self.name,
            supported_query_fields=("search_term",),
            bounded_execution=self.bounded,
        )

    def search(self, query: JobQuery) -> SourceSearchResult:
        self.gate.wait(timeout=2)
        return SourceSearchResult(source=self.name, status="empty", completeness="known_complete")

    def fetch(self, query: JobQuery) -> list[RawJobPost]:
        return []

    def get_detail(self, source_url: str) -> SourceDetailResult:
        return SourceDetailResult(source=self.name, status="unsupported")


async def test_bounded_sources_overlap_in_requested_order() -> None:
    gate = threading.Barrier(2)
    results = await execute_sources(
        [Source("a", gate), Source("b", gate)],
        JobQuery(search_term="x"),
        controller=None,
        concurrency=2,
    )
    assert [r.source for r in results] == ["a", "b"]


async def test_concurrency_one_never_starts_second_source_early() -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()

    class SerialSource(Source):
        def search(self, query: JobQuery) -> SourceSearchResult:
            if self.name == "first":
                first_started.set()
                release_first.wait(timeout=2)
            else:
                second_started.set()
            return SourceSearchResult(
                source=self.name, status="empty", completeness="known_complete"
            )

    gate = threading.Barrier(1)
    task = asyncio.create_task(
        execute_sources(
            [SerialSource("first", gate), SerialSource("second", gate)],
            JobQuery(search_term="x"),
            controller=None,
            concurrency=1,
        )
    )
    await to_thread.run_sync(first_started.wait)
    assert not second_started.is_set()
    release_first.set()
    assert [result.source for result in await task] == ["first", "second"]


def test_jobspy_is_explicitly_not_concurrency_capable() -> None:
    """Thread cancellation cannot establish a JobSpy request timeout."""
    assert JobSpySource(["indeed"]).capabilities.bounded_execution is False


async def test_admission_is_token_owned_and_busy_is_unavailable() -> None:
    redis = Redis()
    controller = SourceAdmissionController(redis, wait_seconds=0, poll_seconds=0)
    first = await controller.acquire("indeed")
    assert first is not None
    assert await controller.acquire("indeed") is None
    redis.values[first.key] = "successor"
    assert not await controller.release(first)
    assert redis.values[first.key] == "successor"
    different_source = await controller.acquire("linkedin")
    assert different_source is not None
    assert await controller.release(different_source)
    assert not await controller.release(different_source)


async def test_admission_renewal_is_token_owned() -> None:
    redis = Redis()
    controller = SourceAdmissionController(redis, wait_seconds=0, poll_seconds=0)
    admission = await controller.acquire("indeed")
    assert admission is not None
    assert await controller.renew(admission)
    redis.values[admission.key] = "successor"
    assert not await controller.renew(admission)


async def test_bounded_admission_wait_returns_retryable_source_busy() -> None:
    redis = Redis()
    controller = SourceAdmissionController(redis, wait_seconds=0, poll_seconds=0)
    admission = await controller.acquire("indeed")
    assert admission is not None

    result = await execute_sources(
        [Source("indeed", threading.Barrier(1))],
        JobQuery(search_term="x"),
        controller=controller,
        concurrency=2,
    )

    assert [(item.status, item.reason_code) for item in result] == [("unavailable", "source_busy")]
