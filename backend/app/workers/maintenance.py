"""Scheduled maintenance: archive stale job postings (Phase 2)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.jobs.repository import JobRepository
from app.infrastructure.database import async_session_factory
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


async def _archive_old_jobs_async() -> dict[str, int]:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=settings.job_archive_after_days)
    async with async_session_factory() as db:
        archived = await JobRepository(db).archive_older_than(cutoff)
        await db.commit()
    logger.info("archive.done", archived=archived, cutoff=cutoff.isoformat())
    return {"archived": archived}


@celery_app.task(
    name="archive_old_jobs",
    soft_time_limit=300,
    time_limit=420,
)
def archive_old_jobs() -> dict[str, int]:
    """Archive postings older than ``job_archive_after_days``."""
    return asyncio.run(_archive_old_jobs_async())
