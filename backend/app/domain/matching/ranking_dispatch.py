"""Redis-backed dispatch policy for user-scoped match ranking."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import redis.asyncio as aioredis

from app.infrastructure.task_store import TaskStore

_PROFILE_DEBOUNCE_SECONDS = 10 * 60
_MANUAL_COOLDOWN_SECONDS = 10 * 60
_ACTIVE_LOCK_SECONDS = 20 * 60


@dataclass(frozen=True)
class RankingDispatch:
    task_id: str | None
    state: str
    retry_after_seconds: int | None = None


class RankingDispatcher:
    """Coalesce profile updates and single-flight manual ranking requests."""

    def __init__(self, redis: aioredis.Redis, task_store: TaskStore) -> None:
        self._redis = redis
        self._task_store = task_store

    @staticmethod
    def profile_key(user_id: int) -> str:
        return f"match-ranking:profile-debounce:{user_id}"

    @staticmethod
    def manual_key(user_id: int) -> str:
        return f"match-ranking:manual-cooldown:{user_id}"

    @staticmethod
    def active_key(user_id: int) -> str:
        return f"match-ranking:active:{user_id}"

    async def schedule_profile_refresh(self, user_id: int) -> str:
        """Schedule one trailing refresh; stale delayed tasks safely no-op."""
        token = str(uuid.uuid4())
        task_id = await self._task_store.create("match_ranking", user_id=user_id)
        await self._redis.set(
            self.profile_key(user_id), token, ex=_PROFILE_DEBOUNCE_SECONDS * 2
        )
        from app.workers.match_ranking import run_match_ranking_for_all_users

        try:
            run_match_ranking_for_all_users.apply_async(
                args=[user_id, task_id, token, False], countdown=_PROFILE_DEBOUNCE_SECONDS
            )
        except Exception:
            await self._compare_delete(self.profile_key(user_id), token)
            await self._task_store.update(
                task_id,
                status="failed",
                message="Could not queue ranking",
                error="Queue unavailable",
            )
            raise
        return task_id

    async def request_manual_refresh(self, user_id: int) -> RankingDispatch:
        """Queue at most one manual refresh in the cooldown window."""
        existing_active_task_id = await self._redis.get(self.active_key(user_id))
        if existing_active_task_id:
            return RankingDispatch(task_id=str(existing_active_task_id), state="active")

        existing_task_id = await self._redis.get(self.manual_key(user_id))
        if existing_task_id:
            ttl = await self._redis.ttl(self.manual_key(user_id))
            return RankingDispatch(
                task_id=str(existing_task_id),
                state="cooldown",
                retry_after_seconds=max(0, int(ttl)),
            )

        task_id = await self._task_store.create("match_ranking", user_id=user_id)
        # Reserve the execution lock before publishing the Celery task.  A
        # profile task that wakes concurrently will observe this task ID and
        # skip, rather than making the user's explicit refresh lose the race.
        acquired = await self._redis.set(
            self.active_key(user_id), task_id, nx=True, ex=_ACTIVE_LOCK_SECONDS
        )
        if not acquired:
            existing_active_task_id = await self._redis.get(self.active_key(user_id))
            return RankingDispatch(task_id=str(existing_active_task_id), state="active")

        await self._redis.set(
            self.manual_key(user_id), task_id, ex=_MANUAL_COOLDOWN_SECONDS
        )

        # A deliberate refresh supersedes any delayed profile refresh.
        await self._redis.delete(self.profile_key(user_id))
        from app.workers.match_ranking import run_match_ranking_for_all_users

        try:
            run_match_ranking_for_all_users.delay(user_id, task_id, None, True)
        except Exception:
            await self._compare_delete(self.active_key(user_id), task_id)
            await self._redis.delete(self.manual_key(user_id))
            await self._task_store.update(
                task_id,
                status="failed",
                message="Could not queue ranking",
                error="Queue unavailable",
            )
            raise
        return RankingDispatch(task_id=task_id, state="queued")

    async def _compare_delete(self, key: str, token: str) -> None:
        await self._redis.eval(  # type: ignore[misc]
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            key,
            token,
        )
