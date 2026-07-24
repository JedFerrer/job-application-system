"""Scheduled scraping driven by shared scrape queries (Phase 5).

## Architecture

User preferences are normalised into ``scrape_queries`` rows keyed by a
canonical ``normalized_hash``.  Multiple users subscribe to the same query.

Beat fires ``dispatch_shared_scrape_queries`` which picks due queries and fans
out one ``scrape_single_query`` task per query.  Each query is scraped ONCE;
results are linked to all subscribed users and match-ranking follows.

## Guards

1. **Per-query Redis lock** — prevents concurrent workers from scraping the
   same query simultaneously.

2. **Search-level freshness** — a Redis key (``scrape:fresh:{hash}``) set after
   each successful external API call prevents re-scraping within the source TTL
   window.  ``force=True`` bypasses this guard.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import redis as _sync_redis
import redis.asyncio as aioredis
from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.jobs.embedding_coordinator import JobEmbeddingCoordinator
from app.domain.jobs.repository import JobRepository
from app.domain.jobs.schemas import IngestRequest
from app.domain.jobs.service import JobService
from app.domain.matching.job_skill_mention_repository import JobSkillMentionRepository
from app.domain.matching.job_skill_mentions import JobSkillMentionService
from app.domain.scraping.repository import ScrapeQueryRepository, SubscriptionRepository
from app.models.scrape_query import ScrapeQuery
from app.models.user_scrape_query_subscription import UserScrapeQuerySubscription
from app.workers.celery_app import celery_app


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


_worker_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_worker_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return a session factory, creating one lazily per process.

    Celery forks workers, so a module-level engine created at import time
    carries internal state (event-loop bindings, connection bookkeeping)
    from the parent process that is invalid in the forked child.  Creating
    the engine lazily inside each task (or on first use per process) avoids
    this.
    """
    global _worker_session_factory
    if _worker_session_factory is None:
        _worker_session_factory = _make_worker_session_factory()
    return _worker_session_factory

logger = get_logger(__name__)

# Per-source freshness TTL in seconds.  A query's next_scrape_at is computed
# as last_scraped_at + max(TTL of its sources).
_SOURCE_TTL_SECONDS: dict[str, int] = {
    "indeed": 2 * 3600,
    "linkedin": 6 * 3600,
    "glassdoor": 6 * 3600,
    "zip_recruiter": 2 * 3600,
}


def _query_ttl_seconds(sources: list[str]) -> int:
    """Return the longest freshness window across the query's sources."""
    return max((_SOURCE_TTL_SECONDS.get(s, 3600) for s in sources), default=3600)


def _freshness_key(normalized_hash: str) -> str:
    return f"scrape:fresh:{normalized_hash}"


def _request_from_query(query: ScrapeQuery) -> IngestRequest:
    """Build an ``IngestRequest`` from a ``ScrapeQuery`` row."""
    return IngestRequest(
        search_term=query.search_term,
        location=query.location,
        is_remote=query.is_remote,
        hours_old=query.hours_old,
        results_wanted=query.results_wanted,
        sources=list(query.sources) or None,
    )


# ---------------------------------------------------------------------------
# Dispatcher (Beat entry-point)
# ---------------------------------------------------------------------------


async def _dispatch_shared_queries_async() -> dict[str, int]:
    """Pick due ``ScrapeQuery`` rows and enqueue one task per query."""
    now = datetime.now(UTC)

    async with _get_worker_session_factory()() as db:
        repo = ScrapeQueryRepository(db)
        due = await repo.get_due_queries(now=now, limit=50)

    for query in due:
        scrape_single_query.delay(str(query.id))

    logger.info("dispatch.done", queries=len(due))
    return {"queries": len(due)}


@celery_app.task(
    name="dispatch_shared_scrape_queries",
    soft_time_limit=60,
    time_limit=120,
)
def dispatch_shared_scrape_queries() -> dict[str, int]:
    """Fan out one scrape task per due shared query.  Called by Beat."""
    return asyncio.run(_dispatch_shared_queries_async())


# ---------------------------------------------------------------------------
# Single-query scrape
# ---------------------------------------------------------------------------


async def _scrape_single_query_async(
    query_id: str, *, force: bool = False
) -> dict[str, int]:
    settings = get_settings()
    ttl_seconds: int = 3600
    now = datetime.now(UTC)

    totals = {"inserted": 0, "updated": 0, "subscribers": 0}
    subscriber_ids: list[int] = []
    embedded_job_ids: list[int] = []

    redis: aioredis.Redis = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, decode_responses=True
    )
    try:
        async with _get_worker_session_factory()() as db:
            query_repo = ScrapeQueryRepository(db)
            sub_repo = SubscriptionRepository(db)

            # Load the query row — it may have been deleted between dispatch
            # and execution.
            result = await db.execute(
                select(ScrapeQuery).where(ScrapeQuery.id == query_id)
            )
            query = result.scalar_one_or_none()
            if query is None or query.status != "active":
                logger.info("scrape.query_gone", query_id=query_id)
                return totals

            # Freshness guard: if another worker scraped this query recently
            # and its freshness TTL hasn't expired, skip.
            if not force and query.last_scraped_at is not None:
                ttl_seconds = _query_ttl_seconds(list(query.sources))
                expiry = query.last_scraped_at + timedelta(seconds=ttl_seconds)
                if now < expiry:
                    logger.info(
                        "scrape.still_fresh",
                        query_id=query_id,
                        hash=query.normalized_hash,
                    )
                    return totals

            request = _request_from_query(query)
            effective_sources = list(
                request.sources or settings.enabled_job_sources
            )

            service = JobService(
                JobRepository(db),
                user_timezone=settings.user_timezone,
                embedding_coordinator=JobEmbeddingCoordinator(JobRepository(db), redis),
                skill_mention_service=JobSkillMentionService(
                    JobSkillMentionRepository(db)
                ),
            )

            # Record the moment before ingest so we can link subscribers to
            # every job that was touched (inserted or updated) by this run.
            scrape_start = datetime.now(UTC)

            try:
                result_obj = await service.ingest(
                    request,
                    default_sources=settings.enabled_job_sources,
                    default_country=settings.job_search_country,
                    default_results=settings.job_results_wanted,
                    proxies=settings.proxy_list or None,
                    # No user_id — we link subscribers after ingest.
                    user_id=None,
                    preference_id=None,
                )
            except Exception:
                logger.warning(
                    "scrape.request_failed",
                    query_id=query_id,
                    search_term=request.search_term,
                    exc_info=True,
                )
                return totals

            totals["inserted"] += result_obj.inserted
            totals["updated"] += result_obj.updated

            # Link every subscriber to the jobs fetched in this run.
            subscriber_ids = await sub_repo.get_subscribers(query_id)
            link_count = 0
            if subscriber_ids:
                link_count = await service.link_recent_jobs_batch(
                    user_ids=subscriber_ids, since=scrape_start
                )
            totals["subscribers"] = link_count
            embedded_job_ids = result_obj.embedded_job_ids

            # Mark the query as scraped with a fresh next_scrape_at.
            ttl_seconds = _query_ttl_seconds(effective_sources)
            next_at = now + timedelta(seconds=ttl_seconds)
            await query_repo.mark_scraped(
                query_id, scraped_at=now, next_scrape_at=next_at
            )

            # Set Redis freshness key to match the TTL.
            if not force:
                await redis.set(
                    _freshness_key(query.normalized_hash), "1", ex=ttl_seconds
                )

            await db.commit()
    finally:
        await redis.aclose()

    logger.info("scrape.done", query_id=query_id, **totals)

    # Score only jobs embedded by this scrape for the affected subscribers.
    if subscriber_ids and embedded_job_ids:
        from app.workers.match_ranking import run_incremental_match_ranking

        run_incremental_match_ranking.delay(subscriber_ids, embedded_job_ids)

    return totals


@celery_app.task(
    name="scrape_single_query",
    soft_time_limit=1500,
    time_limit=1800,
)
def scrape_single_query(query_id: str, force: bool = False) -> dict[str, int]:
    """Scrape one shared query and link results to all subscribers.

    Parameters
    ----------
    query_id:
        The ``ScrapeQuery.id`` to scrape.
    force:
        ``True`` skips the freshness guard.  Set by on-demand refresh so the
        user always gets a fresh batch.  Scheduled runs always use ``False``.

    A per-query Redis lock prevents concurrent workers from scraping the same
    query simultaneously.  After scraping, match ranking is chained per
    subscriber.
    """
    settings = get_settings()
    lock_key = f"lock:scrape_query:{query_id}"
    r = _sync_redis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, decode_responses=True
    )

    acquired = r.set(lock_key, "1", nx=True, ex=1600)
    if not acquired:
        logger.info("scrape.skip_already_running", query_id=query_id)
        return {"inserted": 0, "updated": 0, "subscribers": 0}

    try:
        totals = asyncio.run(_scrape_single_query_async(query_id, force=force))
    finally:
        r.delete(lock_key)

    return totals


# ---------------------------------------------------------------------------
# On-demand refresh (backward-compat wrapper)
# ---------------------------------------------------------------------------


async def _scrape_user_on_demand_async(user_id: int) -> dict[str, int]:
    """Scrape all queries a user is subscribed to (force mode).

    Called by the on-demand refresh endpoint.  We find the user's
    subscriptions and dispatch one forced scrape per subscribed query.
    """
    async with _get_worker_session_factory()() as db:
        result = await db.execute(
            select(distinct(UserScrapeQuerySubscription.scrape_query_id)).where(
                UserScrapeQuerySubscription.user_id == user_id
            )
        )
        query_ids = [str(row) for row in result.scalars().all()]

    for qid in query_ids:
        scrape_single_query.delay(qid, force=True)

    logger.info("scrape.on_demand_dispatched", user_id=user_id, queries=len(query_ids))
    return {"queries": len(query_ids)}


@celery_app.task(
    name="scrape_user_on_demand",
    soft_time_limit=1500,
    time_limit=1800,
)
def scrape_user_on_demand(user_id: int) -> dict[str, int]:
    """Force-scrape all queries subscribed by *user_id*.

    Replaces the old ``scrape_all_active_preferences(user_id, force=True)``
    call from the on-demand refresh endpoint.
    """
    return asyncio.run(_scrape_user_on_demand_async(user_id))
