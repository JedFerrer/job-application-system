"""Authorization regression tests — scoped job lookups.

Verifies that :meth:`JobRepository.get_visible_by_id` and all downstream
service callers enforce the same visibility rules as ``list_jobs``:

- Public jobs are visible to all authenticated users.
- Private jobs are visible ONLY to their owner (``created_by_user_id``).
- Archived jobs are invisible even to the owner.
- Unauthorized access returns 404, never 403.

Requires a running PostgreSQL instance.
"""

from __future__ import annotations

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.applications.repository import ApplicationRepository
from app.domain.applications.service import ApplicationService
from app.domain.jobs.dedup import job_fingerprint
from app.domain.jobs.exceptions import JobNotFoundError
from app.domain.jobs.repository import JobRepository
from app.domain.jobs.service import JobService
from app.models.job import Job
from app.models.user import User


# ── DB fixtures (self-contained, same pattern as test_feed_separation.py) ─


@pytest.fixture(scope="module")
def engine():
    """Module-scoped async engine with NullPool."""
    settings = get_settings()
    return create_async_engine(settings.database_url, poolclass=NullPool, echo=False)


@pytest.fixture(autouse=True)
async def clean_db(engine) -> None:  # noqa: ANN001
    """Truncate all user-data tables before each test."""
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    tables = [
        "user_job_matches",
        "user_jobs",
        "user_job_imports",
        "application_form_sessions",
        "application_form_fields",
        "application_events",
        "applications",
        "cover_letters",
        "interview_preps",
        "candidate_profiles",
        "resumes",
        "extension_sessions",
        "user_job_preferences",
        "user_scrape_query_subscriptions",
        "scrape_queries",
        "job_source_links",
        "jobs",
        "users",
    ]
    async with session_factory() as db:
        for table in tables:
            await db.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        await db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────

_JOB_BASE = {
    "raw_response": {},
    "source_url": "https://example.com/default",
    "source": "test",
}


def _session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _create_user(db: AsyncSession, *, user_id: int, email: str, name: str) -> None:
    await db.execute(
        insert(User).values(
            id=user_id,
            email=email,
            name=name,
            is_active=True,
        )
    )
    await db.commit()


async def _create_job(
    db: AsyncSession,
    *,
    job_id: int,
    visibility: str = "public",
    created_by_user_id: int | None = None,
    status: str = "active",
    title: str = "Test Job",
    company: str = "Test Corp",
) -> Job:
    fp = job_fingerprint(company, title, f"location-{job_id}")
    await db.execute(
        insert(Job).values(
            id=job_id,
            fingerprint=fp,
            title=title,
            company=company,
            location="Remote",
            source="test",
            source_url=f"https://example.com/job/{job_id}",
            visibility=visibility,
            created_by_user_id=created_by_user_id,
            ingest_source="manual" if visibility == "private" else "scraper",
            status=status,
            raw_response={},
        )
    )
    await db.commit()
    result = await db.execute(select(Job).where(Job.id == job_id))
    return result.scalar_one()


# ── get_visible_by_id — repository-level tests ────────────────────────────


async def test_owner_can_access_private_job(engine) -> None:  # noqa: ANN001
    """The owner of a private job can retrieve it via get_visible_by_id."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="owner@test.com", name="Owner")
        await _create_job(
            db, job_id=100, visibility="private", created_by_user_id=1,
        )

    async with sf() as db:
        repo = JobRepository(db)
        job = await repo.get_visible_by_id(user_id=1, job_id=100)
        assert job is not None
        assert job.id == 100
        assert job.visibility == "private"
        assert job.created_by_user_id == 1


async def test_non_owner_cannot_access_private_job(engine) -> None:  # noqa: ANN001
    """A user who does not own a private job gets None from get_visible_by_id."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="owner@test.com", name="Owner")
        await _create_user(db, user_id=2, email="other@test.com", name="Other")
        await _create_job(
            db, job_id=101, visibility="private", created_by_user_id=1,
        )

    async with sf() as db:
        repo = JobRepository(db)
        job = await repo.get_visible_by_id(user_id=2, job_id=101)
        assert job is None


async def test_any_user_can_access_public_job(engine) -> None:  # noqa: ANN001
    """Any authenticated user can retrieve a public job via get_visible_by_id."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="a@test.com", name="User A")
        await _create_user(db, user_id=2, email="b@test.com", name="User B")
        await _create_job(
            db, job_id=102, visibility="public", created_by_user_id=None,
        )

    async with sf() as db:
        repo = JobRepository(db)
        job_a = await repo.get_visible_by_id(user_id=1, job_id=102)
        job_b = await repo.get_visible_by_id(user_id=2, job_id=102)
        assert job_a is not None
        assert job_b is not None
        assert job_a.id == 102
        assert job_b.id == 102


async def test_archived_job_not_visible(engine) -> None:  # noqa: ANN001
    """An archived job is invisible even to its owner via get_visible_by_id."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="owner@test.com", name="Owner")
        await _create_job(
            db, job_id=103, visibility="private", created_by_user_id=1,
        )
        # Link the job to the user, then archive it.
        repo = JobRepository(db)
        await repo.link_user_jobs(1, [103])
        await repo.set_user_job_status(1, 103, "archived")
        await db.commit()

    async with sf() as db:
        repo = JobRepository(db)
        job = await repo.get_visible_by_id(user_id=1, job_id=103)
        assert job is None


async def test_nonexistent_job_returns_none(engine) -> None:  # noqa: ANN001
    """get_visible_by_id returns None for a job ID that does not exist."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="u@test.com", name="User")
        repo = JobRepository(db)
        job = await repo.get_visible_by_id(user_id=1, job_id=99999)
        assert job is None


# ── JobService.get_job — service-level tests ──────────────────────────────


async def test_service_get_job_owner_sees_private(engine) -> None:  # noqa: ANN001
    """JobService.get_job returns the job for the owner."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="owner@test.com", name="Owner")
        await _create_job(
            db, job_id=200, visibility="private", created_by_user_id=1,
        )

    async with sf() as db:
        svc = JobService(JobRepository(db))
        result = await svc.get_job(user_id=1, job_id=200)
        assert result.id == 200


async def test_service_get_job_non_owner_gets_404(engine) -> None:  # noqa: ANN001
    """JobService.get_job raises JobNotFoundError for another user's private job."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="owner@test.com", name="Owner")
        await _create_user(db, user_id=2, email="other@test.com", name="Other")
        await _create_job(
            db, job_id=201, visibility="private", created_by_user_id=1,
        )

    async with sf() as db:
        svc = JobService(JobRepository(db))
        with pytest.raises(JobNotFoundError):
            await svc.get_job(user_id=2, job_id=201)


# ── ApplicationService.create — downstream service test ───────────────────


async def test_application_creation_rejects_other_user_private_job(engine) -> None:  # noqa: ANN001
    """ApplicationService.create raises JobNotFoundError for another user's private job."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="owner@test.com", name="Owner")
        await _create_user(db, user_id=2, email="other@test.com", name="Other")
        await _create_job(
            db, job_id=300, visibility="private", created_by_user_id=1,
        )

    async with sf() as db:
        svc = ApplicationService(
            repository=ApplicationRepository(db),
            job_repository=JobRepository(db),
        )
        with pytest.raises(JobNotFoundError):
            await svc.create(user_id=2, job_id=300)


async def test_application_creation_succeeds_for_public_job(engine) -> None:  # noqa: ANN001
    """ApplicationService.create succeeds for a public job (any user)."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="a@test.com", name="User A")
        await _create_job(
            db, job_id=301, visibility="public", created_by_user_id=None,
        )

    async with sf() as db:
        svc = ApplicationService(
            repository=ApplicationRepository(db),
            job_repository=JobRepository(db),
        )
        result = await svc.create(user_id=1, job_id=301)
        assert result.job_id == 301
        assert result.status == "saved"
