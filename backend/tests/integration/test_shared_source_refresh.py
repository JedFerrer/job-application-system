"""Shared refresh links only the explicit public IDs produced by that run."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
import redis.asyncio as aioredis
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, delete, insert, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.jobs.repository import JobRepository
from app.domain.jobs.schemas import IngestRequest, IngestResponse, SourceRunResult
from app.domain.jobs.service import JobService
from app.domain.scraping.repository import ScrapeQueryRepository, initial_source_state_due_at
from app.infrastructure.job_sources.base import RawJobPost
from app.infrastructure.task_store import TaskStore
from app.models.job import Job
from app.models.scrape_query import ScrapeQuery
from app.models.scrape_query_source_state import ScrapeQuerySourceState
from app.models.user import User
from app.models.user_job import UserJob
from app.workers.job_ingestion import _merge_source_results, _record_parent_query

pytestmark = pytest.mark.integration


def _alembic_config() -> Config:
    return Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))


@pytest.mark.asyncio
async def test_shared_refresh_explicit_public_links_exclude_private_imports() -> None:
    """The handoff cannot link a private job merely because it has an ID."""
    marker = uuid4().hex[:10]
    engine = create_async_engine(get_settings().database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    emails = [f"shared-link-{marker}-{index}@example.test" for index in range(2)]

    try:
        async with session_factory() as session:
            users = [
                User(email=email, name=f"Subscriber {index}")
                for index, email in enumerate(emails)
            ]
            session.add_all(users)
            await session.flush()
            owner_id = users[0].id
            assert owner_id is not None

            common_job_values = {
                "company": "Acme",
                "location": "Remote",
                "source": "test",
                "raw_response": {},
                "status": "active",
            }
            public_job_id = (
                await session.execute(
                    insert(Job)
                    .values(
                        **common_job_values,
                        fingerprint=f"pub{marker}"[:16],
                        identity_scope="public",
                        identity_key=f"public:{marker}",
                        title="Public shared listing",
                        source_url=f"https://example.test/public/{marker}",
                        visibility="public",
                        ingest_source="scraper",
                    )
                    .returning(Job.id)
                )
            ).scalar_one()
            second_public_job_id = (
                await session.execute(
                    insert(Job)
                    .values(
                        **common_job_values,
                        fingerprint=f"pubb{marker}"[:16],
                        identity_scope="public",
                        identity_key=f"public-b:{marker}",
                        title="Second public shared listing",
                        source_url=f"https://example.test/public-b/{marker}",
                        visibility="public",
                        ingest_source="scraper",
                    )
                    .returning(Job.id)
                )
            ).scalar_one()
            private_job_id = (
                await session.execute(
                    insert(Job)
                    .values(
                        **common_job_values,
                        fingerprint=f"private{marker}"[:16],
                        identity_scope=f"user:{owner_id}",
                        identity_key=f"private:{marker}",
                        title="Private import",
                        source_url=f"https://example.test/private/{marker}",
                        visibility="private",
                        ingest_source="manual",
                        created_by_user_id=owner_id,
                    )
                    .returning(Job.id)
                )
            ).scalar_one()

            repository = JobRepository(session)
            count_a = await repository.link_public_jobs_batch(
                user_ids=[users[0].id],
                job_ids=[public_job_id, private_job_id],
            )
            count_b = await repository.link_public_jobs_batch(
                user_ids=[users[1].id],
                job_ids=[second_public_job_id, private_job_id],
            )
            await session.commit()

            assert (count_a, count_b) == (1, 1)
            links = set(
                (
                    await session.execute(
                        select(UserJob.user_id, UserJob.job_id).where(
                            UserJob.user_id.in_([user.id for user in users])
                        )
                    )
                ).all()
            )
            assert links == {
                (users[0].id, public_job_id),
                (users[1].id, second_public_job_id),
            }
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Job).where(Job.source_url.like(f"https://example.test/%/{marker}"))
            )
            await session.execute(delete(User).where(User.email.in_(emails)))
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_shared_ingests_link_only_their_committed_public_ids() -> None:
    """Barrier-gated shared work cannot capture peer or private jobs by time window."""
    marker = uuid4().hex[:10]
    engine = create_async_engine(get_settings().database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    emails = [f"concurrent-link-{marker}-{index}@example.test" for index in range(2)]
    started = [threading.Event(), threading.Event()]
    release = threading.Event()

    class GatedSource:
        def __init__(self, source: str, post: RawJobPost, event: threading.Event) -> None:
            self.name = source
            self._post = post
            self._event = event

        def fetch(self, _query: object) -> list[RawJobPost]:
            self._event.set()
            assert release.wait(timeout=10)
            return [self._post]

    sources = {
        "indeed": GatedSource(
            "indeed",
            RawJobPost(
                source="indeed", source_url=f"https://example.test/a/{marker}",
                title="Query A", company="Acme",
            ),
            started[0],
        ),
        "linkedin": GatedSource(
            "linkedin",
            RawJobPost(
                source="linkedin", source_url=f"https://example.test/b/{marker}",
                title="Query B", company="Acme",
            ),
            started[1],
        ),
    }

    async def run_shared(search_term: str, user_id: int) -> IngestResponse:
        async with session_factory() as session:
            service = JobService(JobRepository(session), external_enrichment_enabled=False)
            return await service.ingest(
                IngestRequest(
                    search_term=search_term,
                    sources=["indeed" if search_term == "query-a" else "linkedin"],
                ),
                default_sources=["indeed" if search_term == "query-a" else "linkedin"],
                default_country="Philippines",
                default_results=10,
                proxies=None,
                subscriber_user_ids=[user_id],
            )

    try:
        async with session_factory() as session:
            users = [
                User(email=email, name=f"Concurrent {index}")
                for index, email in enumerate(emails)
            ]
            session.add_all(users)
            await session.commit()
            user_ids = [user.id for user in users]

        with patch(
            "app.domain.jobs.service.build_sources",
            side_effect=lambda names, **_: [sources[names[0]]],
        ):
            task_a = asyncio.create_task(run_shared("query-a", user_ids[0]))
            task_b = asyncio.create_task(run_shared("query-b", user_ids[1]))
            await asyncio.gather(*(asyncio.to_thread(event.wait, 10) for event in started))

            # This private import is created while both provider executions are
            # deliberately blocked. Exact-ID linking must exclude it.
            async with session_factory() as session:
                await session.execute(
                    insert(Job).values(
                        fingerprint=f"private{marker}"[:16],
                        identity_scope=f"user:{user_ids[0]}",
                        identity_key=f"private:{marker}",
                        title="Private overlap import", company="Acme", location="Remote",
                        source="manual", source_url=f"https://example.test/private/{marker}",
                        raw_response={}, status="active", visibility="private",
                        ingest_source="manual", created_by_user_id=user_ids[0],
                    )
                )
                await session.commit()
            release.set()
            result_a, result_b = await asyncio.gather(task_a, task_b)

        assert len(result_a.touched_job_ids) == len(result_b.touched_job_ids) == 1
        assert set(result_a.touched_job_ids).isdisjoint(result_b.touched_job_ids)
        async with session_factory() as session:
            links = set((await session.execute(
                select(UserJob.user_id, UserJob.job_id).where(UserJob.user_id.in_(user_ids))
            )).all())
        assert links == {
            (user_ids[0], result_a.touched_job_ids[0]),
            (user_ids[1], result_b.touched_job_ids[0]),
        }
    finally:
        release.set()
        async with session_factory() as session:
            await session.execute(delete(Job).where(Job.source_url.like(f"https://example.test/%/{marker}")))
            await session.execute(delete(User).where(User.email.in_(emails)))
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_query_source_freshness_is_durable_and_per_source() -> None:
    """Only a known-complete source result advances that source's freshness."""
    marker = uuid4().hex[:10]
    now = datetime.now(UTC)
    engine = create_async_engine(get_settings().database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    normalized_hash = f"source-state-{marker}"

    try:
        async with session_factory() as session:
            query = ScrapeQuery(
                search_term="Backend engineer",
                location="Remote",
                hours_old=24,
                results_wanted=50,
                sources=["indeed", "linkedin", "zip_recruiter", "glassdoor", "google"],
                normalized_hash=normalized_hash,
                active_user_count=1,
                last_scraped_at=now,
                next_scrape_at=now + timedelta(days=1),
            )
            session.add(query)
            await session.flush()
            repo = ScrapeQueryRepository(session)

            assert await repo.due_sources(
                query_id=query.id,
                sources=query.sources,
                now=now,
                country="Philippines",
                result_limit=50,
                force=True,
            ) == ["indeed", "linkedin", "zip_recruiter", "glassdoor", "google"]
            await repo.record_source_result(
                query_id=query.id,
                source="indeed",
                now=now,
                status="success",
                reason_code=None,
                completeness="known_complete",
                fetched=3,
                accepted=3,
                rejected=0,
                country="Philippines",
                result_limit=50,
                ttl_seconds=7200,
            )
            await repo.record_source_result(
                query_id=query.id,
                source="zip_recruiter",
                now=now,
                status="empty",
                reason_code=None,
                completeness="known_complete",
                fetched=0,
                accepted=0,
                rejected=0,
                country="Philippines",
                result_limit=50,
                ttl_seconds=7200,
            )
            await repo.record_source_result(
                query_id=query.id,
                source="glassdoor",
                now=now,
                status="empty",
                reason_code=None,
                completeness="unknown",
                fetched=0,
                accepted=0,
                rejected=0,
                country="Philippines",
                result_limit=50,
                ttl_seconds=7200,
            )
            await repo.record_source_result(
                query_id=query.id,
                source="google",
                now=now,
                status="unavailable",
                reason_code="source_busy",
                completeness="unknown",
                fetched=0,
                accepted=0,
                rejected=0,
                country="Philippines",
                result_limit=50,
                ttl_seconds=7200,
            )
            await repo.record_source_result(
                query_id=query.id,
                source="linkedin",
                now=now,
                status="failed",
                reason_code="provider_error",
                completeness="unknown",
                fetched=0,
                accepted=0,
                rejected=0,
                country="Philippines",
                result_limit=50,
                ttl_seconds=7200,
            )
            await session.commit()

        async with session_factory() as session:
            repo = ScrapeQueryRepository(session)
            assert await repo.due_sources(
                query_id=query.id,
                sources=["indeed", "linkedin", "zip_recruiter", "glassdoor", "google"],
                now=now + timedelta(minutes=16),
                country="Philippines",
                result_limit=50,
                force=False,
            ) == ["linkedin", "glassdoor", "google"]
            assert await repo.due_sources(
                query_id=query.id,
                sources=["indeed"],
                now=now + timedelta(minutes=16),
                country="Canada",
                result_limit=50,
                force=False,
            ) == ["indeed"]
            assert await repo.due_sources(
                query_id=query.id,
                sources=["indeed"],
                now=now + timedelta(minutes=16),
                country="Philippines",
                result_limit=51,
                force=False,
            ) == ["indeed"]
    finally:
        async with session_factory() as session:
            source_query_ids = select(ScrapeQuery.id).where(
                ScrapeQuery.normalized_hash == normalized_hash
            )
            await session.execute(
                delete(ScrapeQuerySourceState).where(
                    ScrapeQuerySourceState.scrape_query_id.in_(source_query_ids)
                )
            )
            await session.execute(
                delete(ScrapeQuery).where(ScrapeQuery.normalized_hash == normalized_hash)
            )
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_due_source_selects_query_despite_legacy_query_freshness() -> None:
    """An earlier-due state wins over the obsolete query-wide timestamp."""
    marker = uuid4().hex[:10]
    now = datetime.now(UTC)
    engine = create_async_engine(get_settings().database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    query_id: str | None = None
    try:
        async with session_factory() as session:
            query = ScrapeQuery(
                search_term="Source-specific refresh",
                location="Remote",
                results_wanted=10,
                sources=["indeed", "linkedin"],
                normalized_hash=f"due-state-{marker}",
                active_user_count=1,
                next_scrape_at=now + timedelta(hours=6),
            )
            session.add(query)
            await session.flush()
            query_id = query.id
            session.add_all([
                ScrapeQuerySourceState(
                    scrape_query_id=query.id,
                    source="indeed",
                    next_due_at=now - timedelta(seconds=1),
                ),
                ScrapeQuerySourceState(
                    scrape_query_id=query.id,
                    source="linkedin",
                    next_due_at=now + timedelta(hours=4),
                ),
            ])
            await session.commit()

        async with session_factory() as session:
            repo = ScrapeQueryRepository(session)
            selected = await repo.get_due_queries(now=now, limit=50)
            assert query_id in [query.id for query in selected]
            assert await repo.due_sources(
                query_id=query_id,
                sources=["indeed", "linkedin"],
                now=now,
                country="Philippines",
                result_limit=10,
                force=False,
            ) == ["indeed"]
    finally:
        if query_id is not None:
            async with session_factory() as session:
                await session.execute(
                    delete(ScrapeQuerySourceState).where(
                        ScrapeQuerySourceState.scrape_query_id == query_id
                    )
                )
                await session.execute(delete(ScrapeQuery).where(ScrapeQuery.id == query_id))
                await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_lazy_unknown_state_is_staggered_persistent_and_idempotent() -> None:
    """Missing persisted state is initialized once without using legacy time."""
    marker = uuid4().hex[:10]
    now = datetime(2026, 9, 8, 12, tzinfo=UTC)
    engine = create_async_engine(get_settings().database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    query_id: str | None = None
    try:
        async with session_factory() as session:
            query = ScrapeQuery(
                search_term="Lazy state",
                location="Remote",
                results_wanted=10,
                sources=["indeed"],
                normalized_hash=f"lazy-state-{marker}",
                active_user_count=1,
                last_scraped_at=now - timedelta(days=5),
                next_scrape_at=now + timedelta(days=5),
            )
            session.add(query)
            await session.flush()
            query_id = query.id
            repo = ScrapeQueryRepository(session)
            first = await repo.get_or_create_source_state(
                query_id=query_id, source="indeed", now=now
            )
            expected = initial_source_state_due_at(query_id=query_id, source="indeed", now=now)
            assert first.latest_status == "unavailable"
            assert first.completeness == "unknown"
            assert first.last_successful_complete_at is None
            assert first.next_due_at == expected
            await session.commit()

        async with session_factory() as session:
            repo = ScrapeQueryRepository(session)
            second = await repo.get_or_create_source_state(
                query_id=query_id, source="indeed", now=now + timedelta(hours=3)
            )
            assert second.next_due_at == expected
            count = (await session.execute(
                select(ScrapeQuerySourceState).where(
                    ScrapeQuerySourceState.scrape_query_id == query_id
                )
            )).scalars().all()
            assert len(count) == 1
    finally:
        if query_id is not None:
            async with session_factory() as session:
                await session.execute(
                    delete(ScrapeQuerySourceState).where(
                        ScrapeQuerySourceState.scrape_query_id == query_id
                    )
                )
                await session.execute(delete(ScrapeQuery).where(ScrapeQuery.id == query_id))
                await session.commit()
        await engine.dispose()


@pytest.mark.migration
def test_0038_backfills_only_persisted_source_names_as_unknown_state() -> None:
    """The real upgrade never promotes legacy query timestamps to freshness."""
    config = _alembic_config()
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    marker = uuid4().hex[:10]
    query_id: str | None = None
    empty_query_id: str | None = None
    try:
        command.upgrade(config, "0037_scoped_job_identity")
        command.downgrade(config, "0037_scoped_job_identity")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO scrape_queries "
                    "(id, search_term, location, hours_old, results_wanted, sources, "
                    "normalized_hash, status, priority, active_user_count, "
                    "last_scraped_at, next_scrape_at) VALUES "
                    "(:id, 'Migration state', 'Remote', 24, 10, :sources, :hash, "
                    "'active', 5, 1, now() - interval '7 days', now() + interval '7 days')"
                ),
                {
                    "id": str(uuid4()),
                    "sources": ["indeed", "linkedin"],
                    "hash": f"migration-{marker}",
                },
            )
            # INSERT has no RETURNING; retrieve the identifier for cleanup.
            query_id = connection.execute(
                text("SELECT id FROM scrape_queries WHERE normalized_hash = :hash"),
                {"hash": f"migration-{marker}"},
            ).scalar_one()
            empty_query_id = str(uuid4())
            connection.execute(
                text(
                    "INSERT INTO scrape_queries "
                    "(id, search_term, location, hours_old, results_wanted, sources, "
                    "normalized_hash, status, priority, active_user_count) VALUES "
                    "(:id, 'No persisted sources', 'Remote', 24, 10, :sources, :hash, "
                    "'active', 5, 1)"
                ),
                {
                    "id": empty_query_id,
                    "sources": [],
                    "hash": f"migration-empty-{marker}",
                },
            )

        command.upgrade(config, "0038_source_refresh_state")
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT source, last_attempt_at, last_successful_complete_at, "
                    "next_due_at, latest_status, completeness, fetched_count, "
                    "accepted_count, rejected_count, "
                    "effective_country, effective_result_limit "
                    "FROM scrape_query_source_states WHERE scrape_query_id = :id ORDER BY source"
                ),
                {"id": query_id},
            ).mappings().all()
        assert [row["source"] for row in rows] == ["indeed", "linkedin"]
        assert all(row["latest_status"] == "unavailable" for row in rows)
        assert all(row["completeness"] == "unknown" for row in rows)
        assert all(row["last_attempt_at"] is None for row in rows)
        assert all(row["last_successful_complete_at"] is None for row in rows)
        assert all(row["next_due_at"] is not None for row in rows)
        assert len({row["next_due_at"] for row in rows}) == 2
        assert all(
            (row["fetched_count"], row["accepted_count"], row["rejected_count"])
            == (0, 0, 0)
            for row in rows
        )
        assert all(
            row["effective_country"] is None
            and row["effective_result_limit"] is None
            for row in rows
        )
        with engine.connect() as connection:
            empty_state_count = connection.execute(
                text(
                    "SELECT count(*) FROM scrape_query_source_states "
                    "WHERE scrape_query_id = :id"
                ),
                {"id": empty_query_id},
            ).scalar_one()
        assert empty_state_count == 0
    finally:
        if query_id is not None or empty_query_id is not None:
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM scrape_queries WHERE id IN (:query_id, :empty_query_id)"),
                    {"query_id": query_id, "empty_query_id": empty_query_id},
                )
        command.upgrade(config, "head")
        engine.dispose()


@pytest.mark.asyncio
async def test_preference_refresh_parent_aggregates_children_once_and_is_terminal() -> None:
    """Concurrent duplicate reports cannot inflate counters or regress completion."""
    redis = aioredis.from_url(  # type: ignore[no-untyped-call]
        get_settings().redis_url, decode_responses=True
    )
    store = TaskStore(redis)
    task_id = await store.create("preference_refresh", user_id=1)
    first = "query-a:indeed"
    second = "query-a:linkedin"
    success = {
        "source": "indeed",
        "status": "success",
        "fetched": 3,
        "inserted": 2,
        "updated": 1,
        "embedded": 1,
    }
    busy = {
        "source": "linkedin",
        "status": "unavailable",
        "reason_code": "source_busy",
        "fetched": 0,
        "inserted": 0,
        "updated": 0,
        "embedded": 0,
    }
    try:
        await store.initialize_refresh_parent(task_id, [first, second])
        await asyncio.gather(
            *(store.record_refresh_child(task_id, second, busy) for _ in range(8))
        )
        running = await store.get(task_id)
        assert running is not None
        assert running["status"] == "running"
        assert running["completed_children"] == 1
        assert running["inserted"] == 0

        await store.record_refresh_child(task_id, first, success)
        terminal = await store.get(task_id)
        assert terminal is not None
        assert terminal["status"] == "incomplete"
        assert terminal["completed_children"] == 2
        assert terminal["inserted"] == 2
        assert [item["source"] for item in terminal["source_results"]] == [
            "indeed",
            "linkedin",
        ]

        await store.record_refresh_child(task_id, first, success)
        assert (await store.get(task_id)) == terminal

        expired_task = await store.create("preference_refresh", user_id=1)
        await store.initialize_refresh_parent(expired_task, [first])
        await redis.delete(TaskStore._key(expired_task))
        await store.record_refresh_child(expired_task, first, success)
        assert await store.get(expired_task) is None

        empty_task = await store.create("preference_refresh", user_id=1)
        await store.initialize_refresh_parent(empty_task, [])
        empty_parent = await store.get(empty_task)
        assert empty_parent is not None
        assert empty_parent["status"] == "done"
        assert empty_parent["source_results"] == []
    finally:
        await redis.delete(TaskStore._key(task_id))
        await redis.aclose()


@pytest.mark.asyncio
async def test_worker_parent_reporting_uses_each_source_committed_counters() -> None:
    """The worker path forwards source-local counters into atomic aggregation."""
    redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)  # type: ignore[no-untyped-call]
    store = TaskStore(redis)
    task_id = await store.create("preference_refresh", user_id=1)
    query_id = f"worker-parent-{uuid4().hex[:8]}"
    try:
        await store.initialize_refresh_parent(
            task_id,
            [f"{query_id}:indeed", f"{query_id}:linkedin", f"{query_id}:glassdoor"],
        )
        mixed_results = _merge_source_results(
            requested_sources=["indeed", "linkedin", "glassdoor"],
            due_sources=["indeed", "glassdoor"],
            executed_results=[
                SourceRunResult(
                    source="indeed", status="success", fetched=4,
                    inserted=3, updated=1, embedded=2, accepted=4,
                    completeness="known_complete",
                ),
                SourceRunResult(
                    source="glassdoor", status="success", fetched=2,
                    inserted=2, embedded=1, accepted=2,
                    completeness="known_complete",
                ),
            ],
            source_states={
                "linkedin": ScrapeQuerySourceState(
                    scrape_query_id=query_id,
                    source="linkedin",
                    next_due_at=datetime.now(UTC),
                    completeness="known_complete",
                ),
            },
        )
        totals = {
            # These aggregate values must never be copied into either child.
            "inserted": 5,
            "updated": 1,
            "source_results": [item.model_dump() for item in mixed_results],
        }
        await _record_parent_query(task_id, query_id, totals, ["indeed", "linkedin", "glassdoor"])
        parent = await store.get(task_id)
        assert parent is not None
        assert (parent["inserted"], parent["updated"], parent["embedded"]) == (5, 1, 3)
        assert [
            (item["source"], item["inserted"], item["updated"], item["embedded"])
            for item in parent["source_results"]
        ] == [
            ("indeed", 3, 1, 2),
            ("linkedin", 0, 0, 0),
            ("glassdoor", 2, 0, 1),
        ]
        assert parent["source_results"][1]["status"] == "skipped_fresh"
        assert parent["status"] == "done"

        await _record_parent_query(task_id, query_id, totals, ["indeed", "linkedin", "glassdoor"])
        assert await store.get(task_id) == parent
    finally:
        await redis.delete(TaskStore._key(task_id))
        await redis.aclose()
