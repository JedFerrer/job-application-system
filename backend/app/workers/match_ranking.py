"""Scheduled match re-ranking across all users (Phase 2)."""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.matching.exceptions import NoActiveProfileError
from app.domain.matching.repository import MatchingRepository
from app.domain.matching.service import MatchingService
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


async def _rank_all_async(user_id: int | None) -> dict[str, int]:
    totals = {"users": 0, "ranked": 0}
    async with _worker_session_factory() as db:
        if user_id is not None:
            user_ids = [user_id]
        else:
            result = await db.execute(select(User.id))
            user_ids = [int(uid) for uid in result.scalars().all()]

        service = MatchingService(MatchingRepository(db))
        for uid in user_ids:
            try:
                response = await service.rank_jobs(uid, _RANK_LIMIT)
            except NoActiveProfileError:
                logger.info("rank.skip_no_profile", user_id=uid)
                continue
            except Exception:  # noqa: BLE001 - one user must not abort the batch
                logger.warning("rank.failed", user_id=uid, exc_info=True)
                await db.rollback()
                continue
            await db.commit()
            totals["users"] += 1
            totals["ranked"] += response.ranked

    logger.info("rank.done", **totals)
    return totals


@celery_app.task(
    name="run_match_ranking_for_all_users",
    soft_time_limit=600,
    time_limit=900,
)
def run_match_ranking_for_all_users(user_id: int | None = None) -> dict[str, int]:
    """Re-rank job matches for every user (or one user when ``user_id`` is set)."""
    return asyncio.run(_rank_all_async(user_id))
