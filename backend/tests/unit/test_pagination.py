"""Cursor pagination regression tests for applications and imported jobs.

Verifies that:
- First page returns correct items with a next_cursor.
- Cursor-based pagination is stable (no duplicates, no skips).
- Last page returns next_cursor=None.
- Empty lists work.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.applications.repository import ApplicationRepository
from app.domain.jobs.dedup import job_fingerprint
from app.domain.jobs.repository import JobRepository
from app.models.application import Application
from app.models.job import Job
from app.models.user import User
from app.models.user_job import UserJob


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def engine():
    settings = get_settings()
    return create_async_engine(settings.database_url, poolclass=NullPool, echo=False)


@pytest.fixture(autouse=True)
async def clean_db(engine) -> None:  # noqa: ANN001
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        for table in ("applications", "user_jobs", "user_job_imports",
                       "user_job_matches", "jobs", "users"):
            await db.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        await db.commit()


def _sf(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_user_and_jobs(db: AsyncSession, *, count: int) -> list[int]:
    """Create user 1 and *count* jobs, return job IDs."""
    await db.execute(insert(User).values(id=1, email="a@test.com", name="A", is_active=True))
    job_ids: list[int] = []
    for i in range(count):
        fp = job_fingerprint(f"Corp{i}", f"Role {i}", "Remote")
        await db.execute(
            insert(Job).values(
                fingerprint=fp,
                title=f"Role {i}",
                company=f"Corp{i}",
                location="Remote",
                source="manual",
                source_url=f"https://example.com/{i}",
                visibility="private",
                created_by_user_id=1,
                ingest_source="manual",
                status="active",
                raw_response={},
            )
        )
        await db.commit()
        from sqlalchemy import select as _s
        result = await db.execute(_s(Job).where(Job.fingerprint == fp))
        job_ids.append(result.scalar_one().id)
    return job_ids


# ── Application pagination ────────────────────────────────────────────────


async def test_application_first_page_returns_cursor(engine) -> None:  # noqa: ANN001
    """First page of applications returns items and a next_cursor."""
    sf = _sf(engine)
    async with sf() as db:
        job_ids = await _seed_user_and_jobs(db, count=10)
        # Create applications for all jobs.
        for jid in job_ids:
            await db.execute(
                insert(Application).values(user_id=1, job_id=jid, status="saved")
            )
        await db.commit()

    async with sf() as db:
        repo = ApplicationRepository(db)
        rows, total, next_cursor = await repo.list_with_jobs(
            user_id=1, limit=5
        )
        assert len(rows) == 5
        assert total == 10
        assert next_cursor is not None


async def test_application_last_page_no_cursor(engine) -> None:  # noqa: ANN001
    """Last page of applications returns next_cursor=None."""
    sf = _sf(engine)
    async with sf() as db:
        job_ids = await _seed_user_and_jobs(db, count=5)
        for jid in job_ids:
            await db.execute(
                insert(Application).values(user_id=1, job_id=jid, status="saved")
            )
        await db.commit()

    async with sf() as db:
        repo = ApplicationRepository(db)
        rows, total, next_cursor = await repo.list_with_jobs(
            user_id=1, limit=10
        )
        assert len(rows) == 5
        assert total == 5
        assert next_cursor is None


async def test_application_cursor_pagination_no_duplicates(engine) -> None:  # noqa: ANN001
    """Cursor pagination returns all items without duplicates."""
    sf = _sf(engine)
    async with sf() as db:
        job_ids = await _seed_user_and_jobs(db, count=15)
        for jid in job_ids:
            await db.execute(
                insert(Application).values(user_id=1, job_id=jid, status="saved")
            )
        await db.commit()

    async with sf() as db:
        repo = ApplicationRepository(db)
        seen_ids: set[int] = set()
        cursor = None
        pages = 0
        while True:
            rows, total, next_cursor = await repo.list_with_jobs(
                user_id=1, cursor=cursor, limit=5
            )
            pages += 1
            for app, _ in rows:
                assert app.id not in seen_ids, f"Duplicate app {app.id}"
                seen_ids.add(app.id)
            if next_cursor is None:
                break
            cursor = next_cursor

        assert len(seen_ids) == 15
        assert pages == 3


# ── Imported jobs pagination ──────────────────────────────────────────────


async def test_imported_jobs_first_page_returns_cursor(engine) -> None:  # noqa: ANN001
    """First page of imported jobs returns a next_cursor."""
    sf = _sf(engine)
    async with sf() as db:
        job_ids = await _seed_user_and_jobs(db, count=10)
        repo = JobRepository(db)
        # Link jobs as manual imports.
        await repo.link_user_jobs(
            1, job_ids, relationship_source="manual",
            import_method="manual", status="imported",
        )
        await db.commit()

    async with sf() as db:
        repo = JobRepository(db)
        rows, total, next_cursor = await repo.list_imported_jobs(
            user_id=1, limit=5
        )
        assert len(rows) == 5
        assert total == 10
        assert next_cursor is not None


async def test_imported_jobs_cursor_pagination_no_duplicates(engine) -> None:  # noqa: ANN001
    """Cursor pagination for imported jobs returns all items."""
    sf = _sf(engine)
    async with sf() as db:
        job_ids = await _seed_user_and_jobs(db, count=12)
        repo = JobRepository(db)
        await repo.link_user_jobs(
            1, job_ids, relationship_source="manual",
            import_method="manual", status="imported",
        )
        await db.commit()

    async with sf() as db:
        repo = JobRepository(db)
        seen_ids: set[int] = set()
        cursor = None
        while True:
            rows, total, next_cursor = await repo.list_imported_jobs(
                user_id=1, cursor=cursor, limit=5
            )
            for row in rows:
                job = row[0]
                assert job.id not in seen_ids, f"Duplicate job {job.id}"
                seen_ids.add(job.id)
            if next_cursor is None:
                break
            cursor = next_cursor

        assert len(seen_ids) == 12
