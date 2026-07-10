"""Cursor pagination regression tests for /matches."""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.jobs.dedup import job_fingerprint
from app.domain.matching.repository import MatchingRepository
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
    await db.execute(insert(User).values(id=user_id, email=email, name=name, is_active=True))


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
            raw_response={},
        )
    )


async def _create_match(
    db: AsyncSession,
    *,
    user_id: int,
    job_id: int,
    vector_score: float,
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


async def test_first_page_returns_cursor_and_has_more(engine) -> None:  # noqa: ANN001
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="viewer@test.com", name="Viewer")
        await _create_user(db, user_id=2, email="other@test.com", name="Other")

        await _create_job(
            db,
            job_id=101,
            title="Top Public",
            company="Acme",
            visibility="public",
            created_by_user_id=None,
            ingest_source="scraper",
        )
        await _create_match(db, user_id=1, job_id=101, vector_score=99.0)

        await _create_job(
            db,
            job_id=102,
            title="Own Private",
            company="Acme",
            visibility="private",
            created_by_user_id=1,
            ingest_source="manual",
        )
        await _link_user_job(
            db,
            user_id=1,
            job_id=102,
            relationship_source="manual",
            import_method="manual",
            status="imported",
        )
        await _create_match(db, user_id=1, job_id=102, vector_score=95.0)

        await _create_job(
            db,
            job_id=103,
            title="Second Public",
            company="Beta",
            visibility="public",
            created_by_user_id=None,
            ingest_source="scraper",
        )
        await _create_match(db, user_id=1, job_id=103, vector_score=95.0)

        await _create_job(
            db,
            job_id=104,
            title="Other Private",
            company="Gamma",
            visibility="private",
            created_by_user_id=2,
            ingest_source="manual",
        )
        await _create_match(db, user_id=1, job_id=104, vector_score=98.0)

        await _create_job(
            db,
            job_id=105,
            title="Archived Own Private",
            company="Delta",
            visibility="private",
            created_by_user_id=1,
            ingest_source="manual",
        )
        await _link_user_job(
            db,
            user_id=1,
            job_id=105,
            relationship_source="manual",
            import_method="manual",
            status="archived",
        )
        await _create_match(db, user_id=1, job_id=105, vector_score=97.0)

        await _create_job(
            db,
            job_id=106,
            title="Lower Public",
            company="Echo",
            visibility="public",
            created_by_user_id=None,
            ingest_source="scraper",
        )
        await _create_match(db, user_id=1, job_id=106, vector_score=80.0)

        await db.commit()

    async with sf() as db:
        repo = MatchingRepository(db)
        rows, total, next_cursor, has_more = await repo.list_matches(user_id=1, limit=2)
        assert total == 4
        assert has_more is True
        assert next_cursor is not None
        assert [job.id for job, _ in rows] == [101, 103]

    async with sf() as db:
        repo = MatchingRepository(db)
        rows, total, next_cursor, has_more = await repo.list_matches(
            user_id=1, limit=2, cursor=next_cursor
        )
        assert total == 4
        assert has_more is False
        assert next_cursor is None
        assert [job.id for job, _ in rows] == [102, 106]


def _encoded_cursor_payload(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode()


@pytest.mark.parametrize(
    "bad_cursor",
    [
        "not valid base64!",
        _encoded_cursor_payload(b"\xff"),
        _encoded_cursor_payload(b"95.0-123"),
        _encoded_cursor_payload(b"not-a-score|123"),
        _encoded_cursor_payload(b"95.0|not-an-id"),
        _encoded_cursor_payload(b"|123"),
        _encoded_cursor_payload(b"95.0|"),
    ],
)
async def test_malformed_cursors_raise_value_error(engine, bad_cursor: str) -> None:  # noqa: ANN001
    sf = _session_factory(engine)
    async with sf() as db:
        repo = MatchingRepository(db)
        with pytest.raises(ValueError, match="Invalid cursor"):
            await repo.list_matches(user_id=1, limit=2, cursor=bad_cursor)


async def test_deprecated_offset_pagination_still_works_for_legacy_clients(engine) -> None:  # noqa: ANN001
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="viewer@test.com", name="Viewer")
        await _create_user(db, user_id=2, email="other@test.com", name="Other")

        for job_id, title, company, visibility, created_by_user_id, ingest_source, score in (
            (301, "Public One", "Acme", "public", None, "scraper", 99.0),
            (302, "Own Private", "Acme", "private", 1, "manual", 95.0),
            (303, "Public Two", "Beta", "public", None, "scraper", 95.0),
            (304, "Other Private", "Gamma", "private", 2, "manual", 98.0),
            (305, "Archived Own Private", "Delta", "private", 1, "manual", 97.0),
            (306, "Public Three", "Echo", "public", None, "scraper", 80.0),
        ):
            await _create_job(
                db,
                job_id=job_id,
                title=title,
                company=company,
                visibility=visibility,
                created_by_user_id=created_by_user_id,
                ingest_source=ingest_source,
            )
            if job_id == 302:
                await _link_user_job(
                    db,
                    user_id=1,
                    job_id=job_id,
                    relationship_source="manual",
                    import_method="manual",
                    status="imported",
                )
            elif job_id == 305:
                await _link_user_job(
                    db,
                    user_id=1,
                    job_id=job_id,
                    relationship_source="manual",
                    import_method="manual",
                    status="archived",
                )
            await _create_match(db, user_id=1, job_id=job_id, vector_score=score)

        await db.commit()

    async with sf() as db:
        repo = MatchingRepository(db)
        rows, total, next_cursor, has_more = await repo.list_matches(user_id=1, limit=2, offset=0)
        assert total == 4
        assert has_more is True
        assert next_cursor is not None
        assert [job.id for job, _ in rows] == [301, 303]

    async with sf() as db:
        repo = MatchingRepository(db)
        rows, total, next_cursor, has_more = await repo.list_matches(user_id=1, limit=2, offset=2)
        assert total == 4
        assert has_more is False
        assert next_cursor is None
        assert [job.id for job, _ in rows] == [302, 306]


async def test_cursor_pagination_stable_and_filtered(engine) -> None:  # noqa: ANN001
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="viewer@test.com", name="Viewer")
        await _create_user(db, user_id=2, email="other@test.com", name="Other")
        await _create_job(
            db,
            job_id=201,
            title="Public Match",
            company="Open",
            visibility="public",
            created_by_user_id=None,
            ingest_source="scraper",
        )
        await _create_job(
            db,
            job_id=202,
            title="Own Private Match",
            company="Open",
            visibility="private",
            created_by_user_id=1,
            ingest_source="manual",
        )
        await _create_job(
            db,
            job_id=203,
            title="Other User Private",
            company="Hidden",
            visibility="private",
            created_by_user_id=2,
            ingest_source="manual",
        )
        await _create_job(
            db,
            job_id=204,
            title="Archived Public",
            company="Old",
            visibility="public",
            created_by_user_id=None,
            ingest_source="scraper",
        )
        await _create_job(
            db,
            job_id=205,
            title="Another Public",
            company="Open",
            visibility="public",
            created_by_user_id=None,
            ingest_source="scraper",
        )
        await _link_user_job(
            db,
            user_id=1,
            job_id=202,
            relationship_source="manual",
            import_method="manual",
            status="imported",
        )
        await _link_user_job(
            db,
            user_id=1,
            job_id=203,
            relationship_source="manual",
            import_method="manual",
            status="imported",
        )
        await _link_user_job(
            db,
            user_id=1,
            job_id=204,
            relationship_source="manual",
            import_method="manual",
            status="archived",
        )
        await _link_user_job(
            db,
            user_id=1,
            job_id=205,
            relationship_source="manual",
            import_method="manual",
            status="imported",
        )
        await _create_match(db, user_id=1, job_id=201, vector_score=98.0)
        await _create_match(db, user_id=1, job_id=202, vector_score=96.0)
        await _create_match(db, user_id=1, job_id=203, vector_score=99.0)
        await _create_match(db, user_id=1, job_id=204, vector_score=97.0)
        await _create_match(db, user_id=1, job_id=205, vector_score=94.0)
        await db.commit()

    async with sf() as db:
        repo = MatchingRepository(db)
        seen: list[int] = []
        cursor = None
        while True:
            rows, total, next_cursor, has_more = await repo.list_matches(
                user_id=1, limit=2, cursor=cursor
            )
            seen.extend(job.id for job, _ in rows)
            if not has_more:
                assert next_cursor is None
                break
            assert next_cursor is not None
            cursor = next_cursor

        assert total == 3
        assert seen == [201, 202, 205]
        assert len(seen) == len(set(seen))
