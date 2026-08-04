"""Scheduled maintenance for jobs and web refresh-session retention."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.auth.web_session_repository import WebSessionRepository
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


async def _purge_web_refresh_sessions_async() -> dict[str, int]:
    settings = get_settings()
    now = datetime.now(UTC)
    revoked_cutoff = now - timedelta(days=settings.web_refresh_session_retention_days)
    never_revoked_cutoff = now - timedelta(
        days=settings.refresh_token_expire_days
        + settings.web_refresh_session_retention_days
    )
    totals = {"deleted": 0, "batches": 0}

    while True:
        async with _worker_session_factory() as db:
            try:
                deleted = await WebSessionRepository(db).delete_unusable_sessions(
                    revoked_before=revoked_cutoff,
                    never_revoked_before=never_revoked_cutoff,
                    batch_size=settings.web_refresh_session_cleanup_batch_size,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

        totals["deleted"] += deleted
        totals["batches"] += 1
        if deleted < settings.web_refresh_session_cleanup_batch_size:
            break

    logger.info(
        "web_refresh_sessions_cleanup.done",
        **totals,
        revoked_cutoff=revoked_cutoff.isoformat(),
        never_revoked_cutoff=never_revoked_cutoff.isoformat(),
    )
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


@celery_app.task(
    name="purge_web_refresh_sessions",
    soft_time_limit=300,
    time_limit=420,
)
def purge_web_refresh_sessions() -> dict[str, int]:
    """Delete web refresh sessions that can no longer authenticate."""
    return asyncio.run(_purge_web_refresh_sessions_async())
