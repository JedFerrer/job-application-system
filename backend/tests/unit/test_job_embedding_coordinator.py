from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.jobs.embedding_coordinator import JobEmbeddingCoordinator

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_on_demand_embedding_is_saved_once_and_clears_prior_failure() -> None:
    job = SimpleNamespace(description="Python platform engineer", description_embedding=None)

    class FakeRepository:
        def __init__(self) -> None:
            self.embeddings: list[list[float]] = []

        async def get_by_id(self, job_id: int) -> object:
            assert job_id == 55
            return job

        async def set_embedding(self, job_id: int, embedding: list[float]) -> None:
            assert job_id == 55
            self.embeddings.append(embedding)
            job.description_embedding = embedding

        async def commit(self) -> None:
            return None

    repository = FakeRepository()
    redis = AsyncMock()
    redis.set.return_value = True

    with patch(
        "app.domain.jobs.embedding_coordinator.embed_text",
        new=AsyncMock(return_value=[0.1, 0.2]),
    ) as embed:
        result = await JobEmbeddingCoordinator(repository, redis).ensure_embedding(55)

    assert result.status == "ready"
    embed.assert_awaited_once_with("Python platform engineer")
    assert repository.embeddings == [[0.1, 0.2]]
    redis.eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_forced_embedding_replaces_an_existing_embedding_before_unlocking() -> None:
    job = SimpleNamespace(description="Updated Python role", description_embedding=[0.0])

    class FakeRepository:
        def __init__(self) -> None:
            self.embeddings: list[list[float]] = []
            self.committed = False

        async def get_by_id(self, job_id: int) -> object:
            return job

        async def set_embedding(self, job_id: int, embedding: list[float]) -> None:
            self.embeddings.append(embedding)
            job.description_embedding = embedding

        async def commit(self) -> None:
            self.committed = True

    repository = FakeRepository()
    redis = AsyncMock()
    redis.set.return_value = True

    async def _release(*args: object) -> int:
        assert repository.committed is True
        return 1

    redis.eval.side_effect = _release

    with patch(
        "app.domain.jobs.embedding_coordinator.embed_text",
        new=AsyncMock(return_value=[0.9, 0.8]),
    ) as embed:
        result = await JobEmbeddingCoordinator(repository, redis).ensure_embedding(57, force=True)

    assert result.status == "ready"
    embed.assert_awaited_once_with("Updated Python role")
    assert repository.embeddings == [[0.9, 0.8]]
    assert repository.committed is True


@pytest.mark.asyncio
async def test_on_demand_embedding_reports_unavailable_without_provider_call() -> None:
    repository = AsyncMock()
    repository.get_by_id.return_value = SimpleNamespace(description="", description_embedding=None)
    redis = AsyncMock()

    with patch("app.domain.jobs.embedding_coordinator.embed_text", new=AsyncMock()) as embed:
        result = await JobEmbeddingCoordinator(repository, redis).ensure_embedding(56)

    assert result.status == "unavailable"
    embed.assert_not_awaited()
    redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_two_repository_sessions_share_one_embedding_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second session observes the first committed vector after waiting."""
    committed: dict[str, list[float] | None] = {"embedding": None}
    lock_held = False
    started = asyncio.Event()
    proceed = asyncio.Event()
    provider_calls = 0

    class RepositorySession:
        async def get_by_id(self, job_id: int) -> object:
            return SimpleNamespace(
                description="Concurrent Python role",
                description_embedding=committed["embedding"],
            )

        async def set_embedding(self, job_id: int, embedding: list[float]) -> None:
            committed["embedding"] = embedding

        async def commit(self) -> None:
            return None

    class SharedRedis:
        async def set(self, *args: object, **kwargs: object) -> bool:
            nonlocal lock_held
            if lock_held:
                return False
            lock_held = True
            return True

        async def eval(self, *args: object) -> int:
            nonlocal lock_held
            assert committed["embedding"] is not None
            lock_held = False
            return 1

    async def _embed(_: str) -> list[float]:
        nonlocal provider_calls
        provider_calls += 1
        started.set()
        await proceed.wait()
        return [0.3, 0.4]

    monkeypatch.setattr("app.domain.jobs.embedding_coordinator._POLL_INTERVAL_SECONDS", 0)
    redis = SharedRedis()
    with patch("app.domain.jobs.embedding_coordinator.embed_text", new=_embed):
        first = asyncio.create_task(
            JobEmbeddingCoordinator(RepositorySession(), redis).ensure_embedding(58)  # type: ignore[arg-type]
        )
        await started.wait()
        second = asyncio.create_task(
            JobEmbeddingCoordinator(RepositorySession(), redis).ensure_embedding(58)  # type: ignore[arg-type]
        )
        proceed.set()
        first_result, second_result = await asyncio.gather(first, second)

    assert first_result.status == "ready"
    assert second_result.status == "ready"
    assert provider_calls == 1
