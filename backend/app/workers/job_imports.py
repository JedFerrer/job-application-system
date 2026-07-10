"""Async embedding and matching for imported jobs (Phase 4 feed separation).

Runs as a Celery task so the import API response is not blocked by the
1-3 second OpenAI embedding call.  Once the embedding is stored the task
triggers match scoring so the imported job appears in /matches.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.jobs.repository import JobRepository
from app.domain.matching.repository import MatchingRepository
from app.domain.matching.service import MatchingService
from app.infrastructure.embeddings import embed_text
from app.models.user_job import UserJob
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


def _make_worker_session_factory() -> async_sessionmaker[AsyncSession]:
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url, poolclass=NullPool, echo=False
    )
    return async_sessionmaker(engine, expire_on_commit=False)


@celery_app.task(
    bind=True,
    soft_time_limit=120,
    max_retries=2,
    default_retry_delay=30,
    name="embed_imported_job",
)
def embed_imported_job(self, job_id: int, user_id: int) -> None:
    """Embed a newly imported job's description, then run match scoring.

    Called after a manual or extension import creates a new job with a
    description.  Failures are retried twice with a 30-second delay.
    """

    async def _run() -> None:
        factory = _make_worker_session_factory()
        async with factory() as db:
            job_repo = JobRepository(db)

            # Fetch the job to get its description.
            job = await job_repo.get_by_id(job_id)
            if job is None or not job.description:
                logger.warning(
                    "embed_imported_job.skipped",
                    job_id=job_id,
                    reason="job missing or no description",
                )
                return

            # Generate and store the embedding.
            try:
                embedding = await embed_text(job.description)
            except Exception as exc:
                logger.error(
                    "embed_imported_job.embed_failed",
                    job_id=job_id,
                    exc_info=True,
                )
                raise self.retry(exc=exc)

            await job_repo.set_embedding(job_id, embedding)
            logger.info("embed_imported_job.embedded", job_id=job_id)

            # Run match scoring so this job appears in /matches.
            match_repo = MatchingRepository(db)
            match_svc = MatchingService(match_repo)
            try:
                await match_svc.rank_jobs(user_id, limit=200)
                # Sync the new match score to user_jobs for the imported panel.
                match_row = await match_repo.get_match(user_id=user_id, job_id=job_id)
                if match_row:
                    await db.execute(
                        update(UserJob)
                        .where(UserJob.user_id == user_id, UserJob.job_id == job_id)
                        .values(
                            match_score=match_row.vector_score,
                            match_reason={
                                "confidence": match_row.confidence,
                                "llm_score": match_row.llm_score,
                            },
                        )
                    )
            except Exception:
                # Best-effort: embedding succeeded, matching is a bonus.
                logger.warning(
                    "embed_imported_job.match_failed",
                    job_id=job_id,
                    user_id=user_id,
                    exc_info=True,
                )

            await db.commit()

    asyncio.run(_run())
