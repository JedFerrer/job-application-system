"""Backfill persisted skill mentions for existing jobs.

The command is deliberately bounded and resumable.  It reads current job
descriptions, refreshes one job per transaction, and reports only aggregate
counts and safe identifiers.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.domain.matching.job_skill_mention_repository import JobSkillMentionRepository
from app.domain.matching.job_skill_mentions import JobSkillMentionService
from app.infrastructure.database import async_session_factory
from app.models.job import Job

logger = get_logger(__name__)
DEFAULT_BATCH_SIZE = 100


@dataclass
class BackfillResult:
    """Safe aggregate result for one backfill invocation."""

    processed: int = 0
    replaced: int = 0
    empty: int = 0
    failed: int = 0
    last_id: int | None = None


MentionServiceFactory = Callable[[AsyncSession], JobSkillMentionService]


def _default_service_factory(session: AsyncSession) -> JobSkillMentionService:
    return JobSkillMentionService(JobSkillMentionRepository(session))


async def _fetch_batch(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    after_id: int,
    batch_size: int,
) -> list[tuple[int, str | None]]:
    async with session_factory() as session:
        result = await session.execute(
            select(Job.id, Job.description)
            .where(Job.id > after_id)
            .order_by(Job.id)
            .limit(batch_size)
        )
        return [(int(job_id), description) for job_id, description in result.all()]


async def run_backfill(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    after_id: int = 0,
    limit: int | None = None,
    session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
    service_factory: MentionServiceFactory = _default_service_factory,
) -> BackfillResult:
    """Backfill jobs in ascending ID order using per-job transactions."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if after_id < 0:
        raise ValueError("after_id must be non-negative")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive when provided")

    result = BackfillResult()
    cursor = after_id
    remaining = limit

    while remaining is None or remaining > 0:
        requested = batch_size if remaining is None else min(batch_size, remaining)
        jobs = await _fetch_batch(session_factory, after_id=cursor, batch_size=requested)
        if not jobs:
            break

        for job_id, description in jobs:
            result.processed += 1
            result.last_id = job_id
            cursor = job_id
            try:
                async with session_factory() as session:
                    service = service_factory(session)
                    async with session.begin():
                        refreshed = await service.refresh_for_description(
                            job_id=job_id, description=description
                        )
                if refreshed:
                    if description and description.strip():
                        result.replaced += 1
                    else:
                        result.empty += 1
                else:
                    result.failed += 1
                    logger.warning(
                        "matching.skill_mentions.backfill_failed",
                        job_id=job_id,
                        error_type="refresh_failed",
                    )
            except Exception as exc:  # noqa: BLE001 - continue bounded batch safely
                result.failed += 1
                logger.warning(
                    "matching.skill_mentions.backfill_failed",
                    job_id=job_id,
                    error_type=type(exc).__name__,
                )

        if remaining is not None:
            remaining -= len(jobs)

    logger.info(
        "matching.skill_mentions.backfill_complete",
        processed=result.processed,
        replaced=result.replaced,
        empty=result.empty,
        failed=result.failed,
        last_id=result.last_id,
    )
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--after-id", type=int, default=0)
    parser.add_argument("--limit", type=int, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        outcome = asyncio.run(
            run_backfill(
                batch_size=args.batch_size,
                after_id=args.after_id,
                limit=args.limit,
            )
        )
    except ValueError as exc:
        _build_parser().error(str(exc))
    return 1 if outcome.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
