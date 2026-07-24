"""Authorization regression tests for /matches visibility and archived-job filtering."""

from __future__ import annotations

import pytest
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.jobs.dedup import job_fingerprint
from app.domain.jobs.repository import JobRepository
from app.domain.jobs.service import JobService
from app.domain.matching.repository import MatchingRepository
from app.domain.matching.exceptions import MatchNotFoundError
from app.domain.matching.service import MatchingService
from app.models.job import Job
from app.models.user import User
from app.models.user_job import UserJob
from app.models.user_job_match import UserJobMatch

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine():
    settings = get_settings()
    return create_async_engine(settings.database_url, poolclass=NullPool, echo=False)


@pytest.fixture(autouse=True)
async def clean_db(engine) -> None:  # noqa: ANN001
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        await db.execute(
            text("TRUNCATE TABLE user_job_matches, user_jobs, jobs, users RESTART IDENTITY CASCADE")
        )
        await db.commit()


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


async def _create_job(
    db: AsyncSession,
    *,
    job_id: int,
    title: str,
    company: str,
    visibility: str,
    created_by_user_id: int | None,
    ingest_source: str,
) -> None:
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
            ingest_source=ingest_source,
            status="active",
            description_embedding=[0.0] * 1536,
            raw_response={},
        )
    )


async def _create_match(
    db: AsyncSession,
    *,
    user_id: int,
    job_id: int,
    vector_score: float = 87.5,
) -> None:
    await db.execute(
        insert(UserJobMatch).values(
            user_id=user_id,
            job_id=job_id,
            vector_score=vector_score,
        )
    )


async def _link_user_job(
    db: AsyncSession,
    *,
    user_id: int,
    job_id: int,
    relationship_source: str,
    import_method: str,
    status: str,
) -> None:
    await db.execute(
        insert(UserJob).values(
            user_id=user_id,
            job_id=job_id,
            relationship_source=relationship_source,
            import_method=import_method,
            status=status,
        )
    )


async def test_private_job_from_another_user_is_excluded(engine) -> None:  # noqa: ANN001
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="owner@test.com", name="Owner")
        await _create_user(db, user_id=2, email="other@test.com", name="Other")
        await _create_job(
            db,
            job_id=101,
            title="Secret Role",
            company="Hidden Corp",
            visibility="private",
            created_by_user_id=1,
            ingest_source="manual",
        )
        await _create_match(db, user_id=2, job_id=101)
        await db.commit()

    async with sf() as db:
        repo = MatchingRepository(db)
        rows, total, next_cursor, has_more = await repo.list_matches(user_id=2, limit=50)
        assert total == 0
        assert rows == []
        assert next_cursor is None
        assert has_more is False


async def test_archived_job_is_excluded(engine) -> None:  # noqa: ANN001
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="owner@test.com", name="Owner")
        await _create_job(
            db,
            job_id=201,
            title="Archived Role",
            company="Old Co",
            visibility="private",
            created_by_user_id=1,
            ingest_source="manual",
        )
        await _link_user_job(
            db,
            user_id=1,
            job_id=201,
            relationship_source="manual",
            import_method="manual",
            status="archived",
        )
        await _create_match(db, user_id=1, job_id=201)
        await db.commit()

    async with sf() as db:
        repo = MatchingRepository(db)
        rows, total, next_cursor, has_more = await repo.list_matches(user_id=1, limit=50)
        assert total == 0
        assert rows == []
        assert next_cursor is None
        assert has_more is False


async def test_current_users_private_imported_matches_are_allowed(engine) -> None:  # noqa: ANN001
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="owner@test.com", name="Owner")
        await _create_job(
            db,
            job_id=301,
            title="Manual Import",
            company="Acme",
            visibility="private",
            created_by_user_id=1,
            ingest_source="manual",
        )
        await _create_job(
            db,
            job_id=302,
            title="Extension Import",
            company="Beta",
            visibility="private",
            created_by_user_id=1,
            ingest_source="extension",
        )
        await _link_user_job(
            db,
            user_id=1,
            job_id=301,
            relationship_source="manual",
            import_method="manual",
            status="imported",
        )
        await _link_user_job(
            db,
            user_id=1,
            job_id=302,
            relationship_source="extension",
            import_method="extension",
            status="imported",
        )
        await _create_match(db, user_id=1, job_id=301, vector_score=91.0)
        await _create_match(db, user_id=1, job_id=302, vector_score=88.0)
        await db.commit()

    async with sf() as db:
        repo = MatchingRepository(db)
        rows, total, next_cursor, has_more = await repo.list_matches(user_id=1, limit=50)
        assert total == 2
        assert {job.title for job, _ in rows} == {"Manual Import", "Extension Import"}
        assert next_cursor is None
        assert has_more is False


async def test_public_matched_jobs_still_appear(engine) -> None:  # noqa: ANN001
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="viewer@test.com", name="Viewer")
        await _create_job(
            db,
            job_id=401,
            title="Public Role",
            company="Open Corp",
            visibility="public",
            created_by_user_id=None,
            ingest_source="scraper",
        )
        await _create_match(db, user_id=1, job_id=401)
        await db.commit()

    async with sf() as db:
        repo = MatchingRepository(db)
        rows, total, next_cursor, has_more = await repo.list_matches(user_id=1, limit=50)
        assert total == 1
        assert len(rows) == 1
        assert rows[0][0].title == "Public Role"
        assert next_cursor is None
        assert has_more is False


async def test_match_summary_returns_only_the_current_users_persisted_match(engine) -> None:  # noqa: ANN001
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="owner@test.com", name="Owner")
        await _create_user(db, user_id=2, email="other@test.com", name="Other")
        await _create_job(
            db,
            job_id=450,
            title="Public Role",
            company="Open Corp",
            visibility="public",
            created_by_user_id=None,
            ingest_source="scraper",
        )
        await _create_match(db, user_id=1, job_id=450, vector_score=91.0)
        await db.commit()

    async with sf() as db:
        service = MatchingService(MatchingRepository(db))
        summary = await service.get_match_summary(user_id=1, job_id=450)
        assert summary is not None
        assert summary.vector_score == 91.0
        assert await service.get_match_summary(user_id=2, job_id=450) is None


async def test_match_summary_rejects_private_and_archived_jobs(engine) -> None:  # noqa: ANN001
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="owner@test.com", name="Owner")
        await _create_user(db, user_id=2, email="other@test.com", name="Other")
        await _create_job(
            db,
            job_id=451,
            title="Private Role",
            company="Hidden Corp",
            visibility="private",
            created_by_user_id=1,
            ingest_source="manual",
        )
        await _create_match(db, user_id=1, job_id=451)
        await _link_user_job(
            db,
            user_id=1,
            job_id=451,
            relationship_source="manual",
            import_method="manual",
            status="archived",
        )
        await db.commit()

    async with sf() as db:
        service = MatchingService(MatchingRepository(db))
        with pytest.raises(MatchNotFoundError):
            await service.get_match_summary(user_id=1, job_id=451)
        with pytest.raises(MatchNotFoundError):
            await service.get_match_summary(user_id=2, job_id=451)


async def test_archiving_public_match_without_user_job_excludes_only_that_user(engine) -> None:  # noqa: ANN001
    """Archive creates the relationship needed to hide a previously unlinked match."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="archiver@test.com", name="Archiver")
        await _create_user(db, user_id=2, email="viewer@test.com", name="Viewer")
        await _create_job(
            db,
            job_id=402,
            title="Archivable Public Role",
            company="Open Corp",
            visibility="public",
            created_by_user_id=None,
            ingest_source="scraper",
        )
        await _create_match(db, user_id=1, job_id=402)
        await _create_match(db, user_id=2, job_id=402)
        await db.commit()

    async with sf() as db:
        await JobService(JobRepository(db)).archive_job(user_id=1, job_id=402)
        await db.commit()

        matches = MatchingRepository(db)
        archived_rows, archived_total, _, _ = await matches.list_matches(user_id=1, limit=50)
        visible_rows, visible_total, _, _ = await matches.list_matches(user_id=2, limit=50)

        assert archived_rows == []
        assert archived_total == 0
        assert [job.id for job, _ in visible_rows] == [402]
        assert visible_total == 1
