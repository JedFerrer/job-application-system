"""Focused policy tests for coalesced match-ranking dispatch."""

from __future__ import annotations

import pytest

from app.domain.matching.ranking_dispatch import RankingDispatcher


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.ttls[key] = ex
        return True

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, -2)

    async def delete(self, key: str) -> int:
        self.values.pop(key, None)
        self.ttls.pop(key, None)
        return 1

    async def eval(self, script: str, numkeys: int, key: str, token: str) -> int:
        del script, numkeys
        if self.values.get(key) == token:
            return await self.delete(key)
        return 0


class FakeTaskStore:
    def __init__(self) -> None:
        self.created: list[tuple[str, int]] = []
        self.updates: list[tuple[str, dict[str, object]]] = []

    async def create(self, task_type: str, user_id: int) -> str:
        self.created.append((task_type, user_id))
        return f"task-{len(self.created)}"

    async def update(self, task_id: str, **fields: object) -> None:
        self.updates.append((task_id, fields))


class FakeTask:
    def __init__(self) -> None:
        self.delayed: list[tuple[object, ...]] = []
        self.scheduled: list[tuple[tuple[object, ...], int]] = []

    def delay(self, *args: object) -> None:
        self.delayed.append(args)

    def apply_async(self, *, args: list[object], countdown: int) -> None:
        self.scheduled.append((tuple(args), countdown))


class FailingScheduledTask(FakeTask):
    def apply_async(self, *, args: list[object], countdown: int) -> None:
        del args, countdown
        raise RuntimeError("broker unavailable")


@pytest.mark.asyncio
async def test_manual_refresh_is_single_flight_and_supersedes_profile_debounce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import match_ranking

    task = FakeTask()
    monkeypatch.setattr(match_ranking, "run_match_ranking_for_all_users", task)
    redis = FakeRedis()
    store = FakeTaskStore()
    dispatcher = RankingDispatcher(redis, store)  # type: ignore[arg-type]

    await dispatcher.schedule_profile_refresh(7)
    assert task.scheduled and RankingDispatcher.profile_key(7) in redis.values

    first = await dispatcher.request_manual_refresh(7)
    second = await dispatcher.request_manual_refresh(7)

    assert first.task_id == "task-2"
    assert first.state == "queued"
    assert second.task_id == "task-2"
    assert second.state == "active"
    assert task.delayed == [(7, "task-2", None, True)]
    assert RankingDispatcher.profile_key(7) not in redis.values


@pytest.mark.asyncio
async def test_profile_updates_replace_the_debounce_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import match_ranking

    task = FakeTask()
    monkeypatch.setattr(match_ranking, "run_match_ranking_for_all_users", task)
    redis = FakeRedis()
    dispatcher = RankingDispatcher(redis, FakeTaskStore())  # type: ignore[arg-type]

    await dispatcher.schedule_profile_refresh(7)
    first_token = redis.values[RankingDispatcher.profile_key(7)]
    await dispatcher.schedule_profile_refresh(7)

    assert redis.values[RankingDispatcher.profile_key(7)] != first_token
    assert len(task.scheduled) == 2
    assert all(countdown == 600 for _, countdown in task.scheduled)
    assert all(args[1] is not None and args[3] is False for args, _ in task.scheduled)


@pytest.mark.asyncio
async def test_profile_dispatch_failure_marks_task_failed_and_clears_debounce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import match_ranking

    monkeypatch.setattr(match_ranking, "run_match_ranking_for_all_users", FailingScheduledTask())
    redis = FakeRedis()
    store = FakeTaskStore()
    dispatcher = RankingDispatcher(redis, store)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="broker unavailable"):
        await dispatcher.schedule_profile_refresh(7)

    assert RankingDispatcher.profile_key(7) not in redis.values
    assert store.updates == [("task-1", {
        "status": "failed", "message": "Could not queue ranking", "error": "Queue unavailable",
    })]


@pytest.mark.asyncio
async def test_manual_refresh_reuses_an_active_profile_ranking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import match_ranking

    monkeypatch.setattr(match_ranking, "run_match_ranking_for_all_users", FakeTask())
    redis = FakeRedis()
    await redis.set(RankingDispatcher.active_key(7), "profile-task", ex=1_200)
    store = FakeTaskStore()

    result = await RankingDispatcher(redis, store).request_manual_refresh(7)  # type: ignore[arg-type]

    assert result.task_id == "profile-task"
    assert result.state == "active"
    assert store.created == []


@pytest.mark.asyncio
async def test_manual_refresh_reports_cooldown_after_the_prior_task_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import match_ranking

    monkeypatch.setattr(match_ranking, "run_match_ranking_for_all_users", FakeTask())
    redis = FakeRedis()
    dispatcher = RankingDispatcher(redis, FakeTaskStore())  # type: ignore[arg-type]

    first = await dispatcher.request_manual_refresh(7)
    await redis.delete(RankingDispatcher.active_key(7))
    second = await dispatcher.request_manual_refresh(7)

    assert first.state == "queued"
    assert second.task_id == first.task_id
    assert second.state == "cooldown"
    assert second.retry_after_seconds == 600
