"""Scheduled match re-ranking across all users (Phase 2)."""

from __future__ import annotations

import asyncio
import uuid

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.matching.exceptions import NoActiveProfileError
from app.domain.matching.repository import MatchingRepository
from app.domain.matching.service import MatchingService
from app.infrastructure.task_store import TaskStore
from app.models.user import User
from app.workers.celery_app import celery_app


def _make_worker_session_factory() -> async_sessionmaker[AsyncSession]:
    _s = get_settings()
    _engine = create_async_engine(_s.database_url, poolclass=NullPool)
    return async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


_worker_session_factory = _make_worker_session_factory()

logger = get_logger(__name__)

# Number of vector-ranked jobs to (re)score per user per run.
_RANK_LIMIT = 200
_LOCK_TTL_SECONDS = 20 * 60


def _lock_key(user_id: int) -> str:
    return f"match-ranking:active:{user_id}"


def _debounce_key(user_id: int) -> str:
    return f"match-ranking:profile-debounce:{user_id}"


async def _compare_delete(redis: aioredis.Redis, key: str, token: str) -> None:
    await redis.eval(  # type: ignore[misc]
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('del', KEYS[1]) else return 0 end",
        1,
        key,
        token,
    )


async def _rank_all_async(
    user_id: int | None,
    task_id: str | None = None,
    debounce_token: str | None = None,
    lock_preacquired: bool = False,
) -> dict[str, int]:
    totals = {"users": 0, "ranked": 0}
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    task_store = TaskStore(redis)
    try:
        if user_id is not None and debounce_token is not None:
            current = await redis.get(_debounce_key(user_id))
            if current != debounce_token:
                if task_id:
                    await task_store.update(
                        task_id, status="skipped", message="Superseded by a newer profile update"
                    )
                return totals
            await _compare_delete(redis, _debounce_key(user_id), debounce_token)

        async with _worker_session_factory() as db:
            if user_id is not None:
                user_ids = [user_id]
            else:
                result = await db.execute(select(User.id))
                user_ids = [int(uid) for uid in result.scalars().all()]

            service = MatchingService(MatchingRepository(db))
            for uid in user_ids:
                # Task IDs are UUIDs; nightly runs need an equally opaque token
                # so a stale worker cannot release a replacement lock.
                token = task_id or str(uuid.uuid4())
                acquired = lock_preacquired and await redis.get(_lock_key(uid)) == token
                if not acquired:
                    acquired = await redis.set(
                        _lock_key(uid), token, nx=True, ex=_LOCK_TTL_SECONDS
                    )
                if not acquired:
                    logger.info("rank.skip_locked", user_id=uid)
                    if task_id:
                        await task_store.update(
                            task_id,
                            status="skipped",
                            message="Rankings already refreshing",
                        )
                    continue
                try:
                    if task_id:
                        await task_store.update(
                            task_id,
                            status="running",
                            message="Refreshing match rankings…",
                        )
                    response = await service.rank_jobs(uid, _RANK_LIMIT)
                    await db.commit()
                except NoActiveProfileError:
                    await db.rollback()
                    logger.info("rank.skip_no_profile", user_id=uid)
                    if task_id:
                        await task_store.update(
                            task_id,
                            status="failed",
                            message="No active profile",
                            error="No active profile",
                        )
                    continue
                except Exception as exc:  # noqa: BLE001 - one user must not abort the batch
                    logger.warning("rank.failed", user_id=uid, exc_info=True)
                    await db.rollback()
                    if task_id:
                        await task_store.update(
                            task_id,
                            status="failed",
                            message="Ranking failed",
                            error=str(exc),
                        )
                    continue
                finally:
                    await _compare_delete(redis, _lock_key(uid), token)
                totals["users"] += 1
                totals["ranked"] += response.ranked
                if task_id:
                    await task_store.update(
                        task_id,
                        status="done",
                        message="Match rankings refreshed",
                        result={"ranked": response.ranked, "profile_id": response.profile_id},
                    )
    finally:
        await redis.aclose()

    logger.info("rank.done", **totals)
    return totals


@celery_app.task(
    name="run_match_ranking_for_all_users",
    soft_time_limit=600,
    time_limit=900,
)
def run_match_ranking_for_all_users(
    user_id: int | None = None,
    task_id: str | None = None,
    debounce_token: str | None = None,
    lock_preacquired: bool = False,
) -> dict[str, int]:
    """Re-rank job matches for every user (or one user when ``user_id`` is set)."""
    return asyncio.run(_rank_all_async(user_id, task_id, debounce_token, lock_preacquired))


async def _rank_incremental_async(user_ids: list[int], job_ids: list[int]) -> dict[str, int]:
    totals = {"users": 0, "ranked": 0}
    if not user_ids or not job_ids:
        return totals
    async with _worker_session_factory() as db:
        service = MatchingService(MatchingRepository(db))
        for user_id in user_ids:
            try:
                ranked = await service.rank_job_ids(
                    user_id=user_id, job_ids=job_ids, limit=_RANK_LIMIT
                )
            except NoActiveProfileError:
                logger.info("rank.incremental_skip_no_profile", user_id=user_id)
                continue
            except Exception:  # noqa: BLE001 - one user must not abort the batch
                logger.warning("rank.incremental_failed", user_id=user_id, exc_info=True)
                await db.rollback()
                continue
            await db.commit()
            totals["users"] += 1
            totals["ranked"] += ranked
    logger.info("rank.incremental_done", **totals)
    return totals


@celery_app.task(
    name="run_incremental_match_ranking",
    soft_time_limit=600,
    time_limit=900,
)
def run_incremental_match_ranking(user_ids: list[int], job_ids: list[int]) -> dict[str, int]:
    """Score only newly embedded jobs for the subscribers that can see them."""
    return asyncio.run(_rank_incremental_async(user_ids, job_ids))
