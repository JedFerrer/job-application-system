"""Scheduled maintenance: archive stale job postings (Phase 2)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.jobs.repository import JobRepository
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


def _make_worker_session_factory() -> async_sessionmaker[AsyncSession]:
    """NullPool engine for Celery workers.

    Each asyncio.run() call creates a fresh event loop.  A shared connection
    pool would hold connections bound to a previous (now-closed) loop, causing
    'Future attached to a different loop' errors on the second task.  NullPool
    disables pooling so every session opens and closes its own connection.
    """
    _s = get_settings()
    _engine = create_async_engine(_s.database_url, poolclass=NullPool)
    return async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


_worker_session_factory = _make_worker_session_factory()


async def _archive_old_jobs_async() -> dict[str, int]:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=settings.job_archive_after_days)
    async with _worker_session_factory() as db:
        archived = await JobRepository(db).archive_older_than(cutoff)
        await db.commit()
    logger.info("archive.done", archived=archived, cutoff=cutoff.isoformat())
    return {"archived": archived}


async def _purge_expired_jobs_async() -> dict[str, int]:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=settings.job_purge_after_days)
    totals = {"scanned": 0, "protected": 0, "purged": 0}
    cursor: tuple[datetime, int] | None = None

    while True:
        async with _worker_session_factory() as db:
            try:
                batch = await JobRepository(db).purge_expired_unreferenced(
                    cutoff=cutoff,
                    batch_size=settings.job_purge_batch_size,
                    after=cursor,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

        for key, value in batch.totals().items():
            totals[key] += value
        if batch.next_cursor is None:
            break
        cursor = batch.next_cursor

    logger.info("purge.done", **totals, cutoff=cutoff.isoformat())
    return totals


@celery_app.task(
    name="archive_old_jobs",
    soft_time_limit=300,
    time_limit=420,
)
def archive_old_jobs() -> dict[str, int]:
    """Archive postings older than ``job_archive_after_days``."""
    return asyncio.run(_archive_old_jobs_async())


@celery_app.task(
    name="purge_expired_jobs",
    soft_time_limit=300,
    time_limit=420,
)
def purge_expired_jobs() -> dict[str, int]:
    """Permanently remove expired jobs past retention with no user content."""
    return asyncio.run(_purge_expired_jobs_async())
