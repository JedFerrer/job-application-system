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
# ruff: noqa: E501

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import redis as _sync_redis
import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.jobs.embedding_coordinator import JobEmbeddingCoordinator
from app.domain.jobs.repository import JobRepository
from app.domain.jobs.schemas import IngestRequest, SourceRunResult
from app.domain.jobs.service import JobService
from app.domain.scraping.repository import ScrapeQueryRepository, SubscriptionRepository
from app.infrastructure.job_sources.execution import SourceAdmissionController
from app.infrastructure.task_store import TaskStore
from app.models.scrape_query import ScrapeQuery
from app.models.scrape_query_source_state import ScrapeQuerySourceState
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


def _merge_source_results(
    *,
    requested_sources: list[str],
    due_sources: list[str],
    executed_results: list[SourceRunResult],
    source_states: dict[str, ScrapeQuerySourceState],
) -> list[SourceRunResult]:
    """Restore requested source order without treating fresh sources as work."""
    executed_by_source = {result.source: result for result in executed_results}
    due_source_names = set(due_sources)
    merged: list[SourceRunResult] = []
    for source in requested_sources:
        result = executed_by_source.get(source)
        if result is not None:
            merged.append(result)
        elif source not in due_source_names:
            state = source_states.get(source)
            merged.append(
                SourceRunResult(
                    source=source,
                    status="skipped_fresh",
                    completeness=(state.completeness if state is not None else "unknown"),
                )
            )
        else:
            # Source execution returns one result per requested due source. If
            # that invariant is ever broken, preserve truthful incompleteness.
            merged.append(SourceRunResult(source=source, status="unavailable"))
    return merged


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
) -> dict[str, Any]:
    settings = get_settings()
    ttl_seconds: int = 3600
    now = datetime.now(UTC)

    totals: dict[str, Any] = {"inserted": 0, "updated": 0, "subscribers": 0}
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

            request = _request_from_query(query)
            effective_sources = list(
                request.sources or settings.enabled_job_sources
            )
            due_sources = await query_repo.due_sources(
                query_id=query_id,
                sources=effective_sources,
                now=now,
                country=request.country or settings.job_search_country,
                result_limit=request.results_wanted or settings.job_results_wanted,
                force=force,
            )
            source_states = await query_repo.get_source_states(
                query_id=query_id, sources=effective_sources
            )
            if not due_sources:
                logger.info("scrape.all_sources_fresh", query_id=query_id)
                totals["outcome"] = "unchanged"
                totals["source_results"] = [
                    item.model_dump()
                    for item in _merge_source_results(
                        requested_sources=effective_sources,
                        due_sources=due_sources,
                        executed_results=[],
                        source_states=source_states,
                    )
                ]
                # ``due_sources`` may have lazily initialized missing source
                # state. Persist that deterministic state before returning so
                # another worker cannot repeatedly treat it as missing.
                await db.commit()
                return totals
            request = request.model_copy(update={"sources": due_sources})

            job_repository = JobRepository(db)
            qa_browser_isolated = settings.qa_unit_1a_browser_fixtures_enabled
            service = JobService(
                job_repository,
                user_timezone=settings.user_timezone,
                embedding_coordinator=(
                    None
                    if qa_browser_isolated
                    else JobEmbeddingCoordinator(job_repository, redis)
                ),
                external_enrichment_enabled=not qa_browser_isolated,
                source_admission_controller=SourceAdmissionController(redis),
                source_fetch_concurrency=settings.job_source_fetch_concurrency,
            )

            try:
                # Resolve subscribers before ingestion so every source's public
                # jobs and subscriber links commit together before embedding.
                subscriber_ids = await sub_repo.get_subscribers(query_id)
                result_obj = await service.ingest(
                    request,
                    default_sources=settings.enabled_job_sources,
                    default_country=settings.job_search_country,
                    default_results=settings.job_results_wanted,
                    proxies=settings.proxy_list or None,
                    qa_unit_1a_browser=qa_browser_isolated,
                    # Shared jobs are linked per source before enrichment.
                    user_id=None,
                    preference_id=None,
                    subscriber_user_ids=subscriber_ids,
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

            for source_result in result_obj.source_results:
                await query_repo.record_source_result(
                    query_id=query_id,
                    source=source_result.source,
                    now=now,
                    status=source_result.status,
                    reason_code=source_result.reason_code,
                    completeness=source_result.completeness,
                    fetched=source_result.fetched,
                    accepted=source_result.accepted,
                    rejected=source_result.rejected,
                    country=request.country or settings.job_search_country,
                    result_limit=request.results_wanted or settings.job_results_wanted,
                    ttl_seconds=_SOURCE_TTL_SECONDS.get(source_result.source, 3600),
                )

            totals["subscribers"] = result_obj.subscriber_links
            embedded_job_ids = result_obj.embedded_job_ids
            totals["source_results"] = [
                item.model_dump()
                for item in _merge_source_results(
                    requested_sources=effective_sources,
                    due_sources=due_sources,
                    executed_results=result_obj.source_results,
                    source_states=source_states,
                )
            ]

            # Legacy aggregate timestamps remain compatibility metadata only.
            # Per-source state above is the sole freshness authority.
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
def scrape_single_query(
    query_id: str,
    force: bool = False,
    parent_task_id: str | None = None,
    parent_sources: list[str] | None = None,
) -> dict[str, Any]:
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

    token = uuid.uuid4().hex
    # Exceeds the worker hard limit; the token prevents a stale worker from
    # deleting a lease reacquired after process death.
    acquired = r.set(lock_key, token, nx=True, ex=1860)
    if not acquired:
        logger.info("scrape.skip_already_running", query_id=query_id)
        totals: dict[str, Any] = {
            "inserted": 0, "updated": 0, "subscribers": 0,
            "outcome": "incomplete",
        }
        if parent_task_id is not None:
            asyncio.run(
                _record_parent_query(parent_task_id, query_id, totals, parent_sources)
            )
        return totals

    try:
        totals = asyncio.run(_scrape_single_query_async(query_id, force=force))
        if parent_task_id is not None:
            asyncio.run(
                _record_parent_query(parent_task_id, query_id, totals, parent_sources)
            )
    finally:
        r.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end return 0",
            1,
            lock_key,
            token,
        )

    return totals


async def _record_parent_query(
    parent_task_id: str,
    query_id: str,
    totals: Mapping[str, object],
    expected_sources: list[str] | None = None,
) -> None:
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)  # type: ignore[no-untyped-call]
    try:
        raw_results = totals.get("source_results", [])
        if not isinstance(raw_results, list):
            raw_results = []
        by_source = {
            str(result.get("source")): dict(result)
            for result in raw_results
            if isinstance(result, dict)
            and result.get("source")
        }
        sources = expected_sources or list(by_source) or ["query"]
        for source in sources:
            payload = by_source.get(source, {"source": source, "status": "unavailable"})
            payload.update({
                "inserted": payload.get("inserted", 0),
                "updated": payload.get("updated", 0),
                "embedded": payload.get("embedded", 0),
            })
            source_name = str(payload.get("source", source))
            await TaskStore(redis).record_refresh_child(
                parent_task_id, f"{query_id}:{source_name}", payload
            )
    finally:
        await redis.aclose()


# ---------------------------------------------------------------------------
# On-demand refresh (backward-compat wrapper)
# ---------------------------------------------------------------------------


async def _scrape_user_on_demand_async(user_id: int, parent_task_id: str | None = None) -> dict[str, int]:
    """Scrape all queries a user is subscribed to (force mode).

    Called by the on-demand refresh endpoint.  We find the user's
    subscriptions and dispatch one forced scrape per subscribed query.
    """
    settings = get_settings()
    async with _get_worker_session_factory()() as db:
        result = await db.execute(
            select(ScrapeQuery.id, ScrapeQuery.sources).join(
                UserScrapeQuerySubscription,
                UserScrapeQuerySubscription.scrape_query_id == ScrapeQuery.id,
            ).where(UserScrapeQuerySubscription.user_id == user_id).distinct()
        )
        query_rows = list(result.all())
        query_sources = [
            (str(row.id), list(row.sources or settings.enabled_job_sources))
            for row in query_rows
        ]

    if parent_task_id is not None:
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)  # type: ignore[no-untyped-call]
        try:
            children = [
                f"{query_id}:{source}"
                for query_id, sources in query_sources
                for source in sources
            ]
            await TaskStore(redis).initialize_refresh_parent(parent_task_id, children)
        finally:
            await redis.aclose()
    for query_id, sources in query_sources:
        scrape_single_query.delay(
            query_id,
            force=True,
            parent_task_id=parent_task_id,
            parent_sources=sources,
        )

    logger.info("scrape.on_demand_dispatched", user_id=user_id, queries=len(query_sources))
    return {"queries": len(query_sources)}


@celery_app.task(
    name="scrape_user_on_demand",
    soft_time_limit=1500,
    time_limit=1800,
)
def scrape_user_on_demand(user_id: int, parent_task_id: str | None = None) -> dict[str, int]:
    """Force-scrape all queries subscribed by *user_id*.

    Replaces the old ``scrape_all_active_preferences(user_id, force=True)``
    call from the on-demand refresh endpoint.
    """
    return asyncio.run(_scrape_user_on_demand_async(user_id, parent_task_id))
