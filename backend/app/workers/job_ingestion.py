"""Scheduled scraping driven by saved user preferences (Phase 2).

Sync Celery entry points bridge to the existing async ``JobService.ingest``
pipeline via ``asyncio.run`` — no parallel sync DB layer is needed.

## Scalability design (multi-user)

Beat fires ``dispatch_all_scrapes`` once per schedule window.  That task reads
all active preferences and fans out **one** ``scrape_all_active_preferences``
task per user.  Celery distributes those tasks across all available workers so
100 users scrape in parallel rather than sequentially.

Two guards prevent redundant work:

1. **Per-user Redis lock** — if a scrape for user X is already running (e.g.
   from an on-demand refresh), the newly dispatched scheduled task exits
   immediately instead of queuing a second identical run.

2. **Search-level deduplication** — a Redis key keyed on the canonical
   (search_term, location, remote, sources, hours_old) tuple is set after each
   successful jobspy call and expires with the cooldown TTL.  If two users share
   an identical search the external API is only hit once; subsequent users skip
   the fetch and rely on the jobs already upserted to the shared ``job_postings``
   table.  ``force=True`` (on-demand refresh) bypasses both guards.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import redis as _sync_redis
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.jobs.repository import JobRepository
from app.domain.jobs.schemas import IngestRequest
from app.domain.jobs.service import JobService
from app.domain.preferences.repository import PreferenceRepository
from app.models.user_job_preference import UserJobPreference
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


_worker_session_factory = _make_worker_session_factory()

logger = get_logger(__name__)


def _is_remote_for(remote_preference: str | None) -> bool | None:
    match remote_preference:
        case "remote":
            return True
        case "onsite":
            return False
        case _:
            return None


def _build_requests(
    preference: UserJobPreference, *, default_results: int
) -> list[IngestRequest]:
    """Fan a preference out into one IngestRequest per (title x location)."""
    locations: list[str | None] = list(preference.locations) or [None]
    is_remote = _is_remote_for(preference.remote_preference)
    results = preference.results_per_source or default_results
    sources = list(preference.enabled_sources) or None

    requests: list[IngestRequest] = []
    for title in preference.job_titles:
        clean_title = title.strip()
        if not clean_title:
            continue
        for location in locations:
            requests.append(
                IngestRequest(
                    search_term=clean_title,
                    location=location,
                    is_remote=is_remote,
                    results_wanted=results,
                    sources=sources,
                    hours_old=preference.hours_old,
                )
            )
    return requests


def _search_dedup_key(request: IngestRequest, effective_sources: list[str]) -> str:
    """Stable Redis key for a unique (term, location, remote, sources, hours_old) search.

    Two users whose preferences produce identical search parameters share the
    same key, so the job board is only hit once within the cooldown window.
    """
    parts = ":".join(
        [
            request.search_term.lower().strip(),
            (request.location or "").lower().strip(),
            str(request.is_remote),
            str(request.hours_old or ""),
            ",".join(sorted(effective_sources)),
        ]
    )
    digest = hashlib.sha256(parts.encode()).hexdigest()[:16]
    return f"scrape:dedup:{digest}"


# ---------------------------------------------------------------------------
# Dispatcher (Beat entry-point for multi-user fan-out)
#
# TODO(shared-scrape-queries): Replace per-user fan-out with shared scrape
# queries.  Each user preference should subscribe to normalized
# ``scrape_queries`` rows.  See docs/job_scraper_scaling_feed_import_plan.md.
# ---------------------------------------------------------------------------


async def _dispatch_all_scrapes_async() -> dict[str, int]:
    """Read all active preference user IDs and enqueue one task per user.

    Users whose preferences haven't been updated in 7 days are skipped to
    avoid scraping for dormant accounts.
    """
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=7)

    async with _worker_session_factory() as db:
        active_prefs = await PreferenceRepository(db).list_active()
        # Filter to users whose preferences have been touched recently.
        user_ids = [
            p.user_id
            for p in active_prefs
            if p.is_active and p.updated_at.replace(tzinfo=UTC) >= cutoff
        ]

    for uid in user_ids:
        scrape_all_active_preferences.delay(uid)

    skipped = len(active_prefs) - len(user_ids)
    logger.info(
        "dispatch.done", users=len(user_ids), skipped=skipped, cutoff=cutoff.isoformat()
    )
    return {"users": len(user_ids), "skipped": skipped}


@celery_app.task(
    name="dispatch_all_scrapes",
    soft_time_limit=60,
    time_limit=120,
)
def dispatch_all_scrapes() -> dict[str, int]:
    """Fan out one scrape task per active user.  Called by Beat.

    Each per-user task runs in parallel across available Celery workers,
    so scrape time scales with worker count rather than user count.
    """
    return asyncio.run(_dispatch_all_scrapes_async())


# ---------------------------------------------------------------------------
# Per-user scrape
# ---------------------------------------------------------------------------


async def _scrape_active_preferences_async(
    user_id: int | None, *, force: bool
) -> dict[str, int]:
    settings = get_settings()
    cooldown = timedelta(minutes=settings.job_refresh_cooldown_minutes)
    # Dedup TTL mirrors the cooldown so keys expire exactly when a fresh scrape
    # would be allowed anyway.
    dedup_ttl = max(int(cooldown.total_seconds()), 60)
    now = datetime.now(UTC)

    totals = {"preferences": 0, "requests": 0, "inserted": 0, "updated": 0}

    # Fresh Redis client per task execution — the shared async pool is bound to
    # the API's event loop which is not running inside a Celery worker.
    redis: aioredis.Redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with _worker_session_factory() as db:
            pref_repo = PreferenceRepository(db)
            if user_id is not None:
                one = await pref_repo.get_for_user(user_id)
                preferences = [one] if one is not None else []
            else:
                preferences = await pref_repo.list_active()

            for preference in preferences:
                if preference is None or not preference.is_active:
                    continue

                # Respect the cooldown unless the user explicitly triggered a
                # refresh (force=True).
                if (
                    not force
                    and preference.last_scraped_at is not None
                    and now - preference.last_scraped_at < cooldown
                ):
                    logger.info("scrape.skip_cooldown", user_id=preference.user_id)
                    continue

                requests = _build_requests(
                    preference, default_results=settings.job_results_wanted
                )
                service = JobService(
                    JobRepository(db), user_timezone=settings.user_timezone
                )
                totals["preferences"] += 1

                for request in requests:
                    totals["requests"] += 1
                    effective_sources = list(
                        request.sources or settings.enabled_job_sources
                    )

                    # Skip the external API call if an identical search was
                    # completed recently by any user.  Jobs are already in the
                    # shared job_postings table; match ranking will surface them.
                    if not force:
                        dedup_key = _search_dedup_key(request, effective_sources)
                        if await redis.exists(dedup_key):
                            logger.info(
                                "scrape.dedup_skip",
                                search_term=request.search_term,
                                location=request.location,
                            )
                            continue

                    try:
                        result = await service.ingest(
                            request,
                            default_sources=settings.enabled_job_sources,
                            default_country=settings.job_search_country,
                            default_results=settings.job_results_wanted,
                            proxies=settings.proxy_list or None,
                            user_id=preference.user_id,
                            preference_id=preference.id,
                        )
                    except Exception:  # noqa: BLE001 - one bad search must not abort the user's batch
                        logger.warning(
                            "scrape.request_failed",
                            user_id=preference.user_id,
                            search_term=request.search_term,
                            exc_info=True,
                        )
                        continue

                    # Mark this search as fresh so duplicate cross-user calls
                    # are skipped for the remainder of the cooldown window.
                    if not force:
                        dedup_key = _search_dedup_key(request, effective_sources)
                        await redis.set(dedup_key, "1", ex=dedup_ttl)

                    totals["inserted"] += result.inserted
                    totals["updated"] += result.updated

                await pref_repo.mark_scraped(preference.user_id, scraped_at=now)
                await db.commit()
    finally:
        await redis.aclose()

    logger.info("scrape.done", **totals)
    return totals


@celery_app.task(
    name="scrape_all_active_preferences",
    soft_time_limit=1500,
    time_limit=1800,
)
def scrape_all_active_preferences(
    user_id: int | None = None, force: bool = False
) -> dict[str, int]:
    """Scrape jobs for one user's active preferences.

    Parameters
    ----------
    user_id:
        The user to scrape.  When dispatched by ``dispatch_all_scrapes`` this
        is always set.  ``None`` retains backward-compatibility for any direct
        calls that process all users in one pass.
    force:
        ``True`` skips the cooldown check and search-level deduplication.
        Set by the on-demand ``/preferences/refresh`` endpoint so the user
        always gets a fresh batch regardless of the last scheduled run.
        Scheduled runs dispatched by ``dispatch_all_scrapes`` always use the
        default ``False``.

    A per-user Redis lock (TTL > soft_time_limit) prevents a concurrent
    on-demand refresh from stacking on top of a scheduled run for the same user.
    After scraping, match ranking is chained so new jobs surface immediately.
    """
    settings = get_settings()
    lock_key = f"lock:scrape_user:{user_id}"
    r = _sync_redis.from_url(settings.redis_url, decode_responses=True)

    # Atomic set-if-not-exists.  TTL is slightly above soft_time_limit so the
    # lock auto-expires if the worker is killed before the finally block runs.
    acquired = r.set(lock_key, "1", nx=True, ex=1600)
    if not acquired:
        logger.info("scrape.skip_already_running", user_id=user_id)
        return {"preferences": 0, "requests": 0, "inserted": 0, "updated": 0}

    try:
        totals = asyncio.run(_scrape_active_preferences_async(user_id, force=force))
    finally:
        r.delete(lock_key)

    # Re-rank matches so freshly ingested jobs surface right away.
    from app.workers.match_ranking import run_match_ranking_for_all_users

    run_match_ranking_for_all_users.delay(user_id)
    return totals
    return totals
