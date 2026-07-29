"""Worker lock behavior for nightly full match ranking."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.workers import match_ranking


class FakeRedis:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int) -> bool:
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script: str, numkeys: int, key: str, token: str) -> int:
        del script, numkeys
        if self.values.get(key) == token:
            self.values.pop(key)
            return 1
        return 0

    async def aclose(self) -> None:
        return None


class FakeDb:
    def __init__(self, user_ids: list[int]) -> None:
        self.user_ids = user_ids
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self) -> FakeDb:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def execute(self, statement: object) -> object:
        del statement
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self.user_ids))

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_nightly_ranking_skips_locked_user_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis({"match-ranking:active:1": "other-task"})
    db = FakeDb([1, 2])

    class Service:
        def __init__(self, repository: object) -> None:
            del repository

        async def rank_jobs(self, user_id: int, limit: int) -> object:
            assert (user_id, limit) == (2, 200)
            return SimpleNamespace(ranked=200)

    monkeypatch.setattr(match_ranking.aioredis, "from_url", lambda *args, **kwargs: redis)  # type: ignore[attr-defined]
    monkeypatch.setattr(match_ranking, "_worker_session_factory", lambda: db)
    monkeypatch.setattr(match_ranking, "MatchingService", Service)

    totals = await match_ranking._rank_all_async(None)

    assert totals == {"users": 1, "ranked": 200}
    assert db.commits == 1
    assert redis.values == {"match-ranking:active:1": "other-task"}


@pytest.mark.asyncio
async def test_failed_ranking_releases_its_opaque_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    db = FakeDb([3])

    class FailingService:
        def __init__(self, repository: object) -> None:
            del repository

        async def rank_jobs(self, user_id: int, limit: int) -> object:
            del user_id, limit
            raise RuntimeError("database failure")

    monkeypatch.setattr(match_ranking.aioredis, "from_url", lambda *args, **kwargs: redis)  # type: ignore[attr-defined]
    monkeypatch.setattr(match_ranking, "_worker_session_factory", lambda: db)
    monkeypatch.setattr(match_ranking, "MatchingService", FailingService)

    assert await match_ranking._rank_all_async(None) == {"users": 0, "ranked": 0}
    assert db.rollbacks == 1
    assert redis.values == {}
