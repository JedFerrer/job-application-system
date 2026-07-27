"""Cursor pagination regression tests for /matches."""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.jobs.dedup import job_fingerprint
from app.domain.matching.repository import MatchingRepository
from app.models.job import Job
from app.models.job_skill_mention import JobSkillMention
from app.models.user import User
from app.models.user_job import UserJob
from app.models.user_job_match import UserJobMatch

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(settings.database_url, poolclass=NullPool, echo=False)


@pytest.fixture(autouse=True)
async def clean_db(engine: AsyncEngine) -> None:
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        await db.execute(
            text("TRUNCATE TABLE user_job_matches, user_jobs, jobs, users RESTART IDENTITY CASCADE")
        )
        await db.commit()


def _session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
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
            description_embedding=[0.0] * 1536,
            raw_response={},
        )
    )


async def _create_match(
    db: AsyncSession,
    *,
    user_id: int,
    job_id: int,
    vector_score: float,
    ranking_score: float | None = None,
    origin: str = "automatic",
) -> None:
    await db.execute(
        insert(UserJobMatch).values(
            user_id=user_id,
            job_id=job_id,
            vector_score=vector_score,
            ranking_score=ranking_score,
            origin=origin,
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


async def test_hybrid_retrieval_applies_visibility_lifecycle_and_embedding_filters(
    engine: AsyncEngine,
) -> None:
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="viewer@test.com", name="Viewer")
        await _create_user(db, user_id=2, email="other@test.com", name="Other")

        jobs = (
            (101, "inactive", "public", None, True),
            (102, "active", "public", None, True),
            (103, "active", "public", None, False),
            (104, "active", "private", 2, True),
            (105, "active", "private", 1, True),
            (106, "active", "public", None, True),
        )
        for job_id, status, visibility, owner_id, embedded in jobs:
            fp = job_fingerprint("Hybrid Test", f"Python Backend {job_id}", f"location-{job_id}")
            await db.execute(
                insert(Job).values(
                    id=job_id,
                    fingerprint=fp,
                    title=f"Python Backend {job_id}",
                    company="Hybrid Test",
                    location="Remote",
                    source="test",
                    source_url=f"https://example.com/job/{job_id}",
                    visibility=visibility,
                    created_by_user_id=owner_id,
                    ingest_source="manual" if visibility == "private" else "scraper",
                    status=status,
                    description="Python backend engineering",
                    description_embedding=[0.0] * 1536 if embedded else None,
                    raw_response={},
                )
            )
            await db.execute(
                insert(JobSkillMention).values(
                    job_id=job_id,
                    skill_id="local:python",
                    canonical_label="Python",
                    classification="required",
                    job_excerpt="Python backend engineering",
                    start_offset=0,
                    end_offset=6,
                    analyzer_version="test",
                )
            )

        await _link_user_job(
            db,
            user_id=1,
            job_id=102,
            relationship_source="global",
            import_method="scraper",
            status="archived",
        )
        await db.commit()

    async with sf() as db:
        repository = MatchingRepository(db)
        text_rows = await repository.search_jobs_by_text("Python backend", 400, user_id=1)
        skill_rows = await repository.search_jobs_by_skills({"local:python"}, 400, user_id=1)

    assert {row.job_id for row in text_rows} == {105, 106}
    assert {row.job_id for row in skill_rows} == {105, 106}


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


async def test_fit_cursor_pagination_orders_by_ranking_score_and_rejects_vector_cursor(
    engine: AsyncEngine,
) -> None:  # noqa: ANN001
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=11, email="fit-viewer@test.com", name="Fit Viewer")
        for job_id in range(401, 406):
            await _create_job(
                db,
                job_id=job_id,
                title=f"Fit Candidate {job_id}",
                company="Fit Test",
                visibility="public",
                created_by_user_id=None,
                ingest_source="scraper",
            )
        await _create_match(db, user_id=11, job_id=401, vector_score=99.0, ranking_score=20.0)
        await _create_match(db, user_id=11, job_id=402, vector_score=20.0, ranking_score=90.0)
        await _create_match(db, user_id=11, job_id=403, vector_score=10.0, ranking_score=90.0)
        await _create_match(db, user_id=11, job_id=404, vector_score=98.0, ranking_score=None)
        await _create_match(db, user_id=11, job_id=405, vector_score=30.0, ranking_score=70.0)
        await db.commit()

    async with sf() as db:
        repo = MatchingRepository(db)
        rows, total, next_cursor, has_more = await repo.list_matches(
            user_id=11, limit=2, ranking_mode="fit"
        )
        assert total == 4
        assert has_more is True
        assert [job.id for job, _ in rows] == [403, 402]
        assert next_cursor is not None
        assert base64.urlsafe_b64decode(next_cursor).decode().startswith("fit|")

        next_rows, _, final_cursor, final_has_more = await repo.list_matches(
            user_id=11, limit=2, cursor=next_cursor, ranking_mode="fit"
        )
        assert [job.id for job, _ in next_rows] == [405, 401]
        assert final_cursor is None
        assert final_has_more is False

        with pytest.raises(ValueError, match="Invalid cursor"):
            await repo.list_matches(user_id=11, limit=2, cursor=next_cursor)

        _, _, vector_cursor, _ = await repo.list_matches(user_id=11, limit=1)
        assert vector_cursor is not None
        with pytest.raises(ValueError, match="Invalid cursor"):
            await repo.list_matches(
                user_id=11, limit=1, cursor=vector_cursor, ranking_mode="fit"
            )


async def test_shadow_listing_uses_legacy_vector_cursor_and_ignores_ranking_score(
    engine: AsyncEngine,
) -> None:
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=12, email="shadow-viewer@test.com", name="Shadow Viewer")
        for job_id in range(421, 425):
            await _create_job(
                db,
                job_id=job_id,
                title=f"Shadow Candidate {job_id}",
                company="Shadow Test",
                visibility="public",
                created_by_user_id=None,
                ingest_source="scraper",
            )
        await _create_match(db, user_id=12, job_id=421, vector_score=99.0, ranking_score=10.0)
        await _create_match(db, user_id=12, job_id=422, vector_score=20.0, ranking_score=99.0)
        await _create_match(db, user_id=12, job_id=423, vector_score=99.0, ranking_score=1.0)
        await _create_match(db, user_id=12, job_id=424, vector_score=98.0, ranking_score=2.0)
        await db.commit()

    async with sf() as db:
        repo = MatchingRepository(db)
        rows, total, next_cursor, has_more = await repo.list_matches(
            user_id=12, limit=2, ranking_mode="shadow"
        )
        assert total == 4
        assert has_more is True
        assert [job.id for job, _ in rows] == [423, 421]
        assert next_cursor is not None
        assert not base64.urlsafe_b64decode(next_cursor).decode().startswith("fit|")

        next_rows, _, _, next_has_more = await repo.list_matches(
            user_id=12, limit=2, cursor=next_cursor, ranking_mode="shadow"
        )
        assert [job.id for job, _ in next_rows] == [424, 422]
        assert next_has_more is False

        fit_cursor = _encoded_cursor_payload(b"fit|99.0|423")
        with pytest.raises(ValueError, match="Invalid cursor"):
            await repo.list_matches(user_id=12, limit=2, cursor=fit_cursor, ranking_mode="shadow")


async def test_fit_pruning_uses_ranking_score_and_keeps_manual_rows(engine: AsyncEngine) -> None:
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=13, email="fit-pruning@test.com", name="Fit Pruning")
        for job_id in range(431, 436):
            await _create_job(
                db,
                job_id=job_id,
                title=f"Fit Pruning Candidate {job_id}",
                company="Fit Pruning Test",
                visibility="public",
                created_by_user_id=None,
                ingest_source="scraper",
            )
        await _create_match(db, user_id=13, job_id=431, vector_score=99.0, ranking_score=99.0)
        await _create_match(db, user_id=13, job_id=432, vector_score=20.0, ranking_score=90.0)
        await _create_match(db, user_id=13, job_id=433, vector_score=30.0, ranking_score=80.0)
        await _create_match(db, user_id=13, job_id=434, vector_score=98.0, ranking_score=None)
        await _create_match(
            db,
            user_id=13,
            job_id=435,
            vector_score=1.0,
            ranking_score=95.0,
            origin="manual",
        )
        await db.commit()

    async with sf() as db:
        repo = MatchingRepository(db)
        await repo.reconcile_fit_automatic_matches(user_id=13, candidate_ids={432, 433})
        await repo.prune_automatic_matches(user_id=13, limit=2, ranking_mode="fit")
        await db.commit()
        rows = await db.execute(
            select(UserJobMatch.job_id, UserJobMatch.origin)
            .where(UserJobMatch.user_id == 13)
            .order_by(UserJobMatch.job_id)
        )

    assert list(rows.tuples().all()) == [
        (432, "automatic"),
        (433, "automatic"),
        (435, "manual"),
    ]


def _encoded_cursor_payload(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode()


@pytest.mark.parametrize("raw_score", ["nan", "inf", "-inf"])
async def test_non_finite_fit_cursors_raise_value_error(
    engine: AsyncEngine, raw_score: str
) -> None:
    sf = _session_factory(engine)
    async with sf() as db:
        repo = MatchingRepository(db)
        cursor = _encoded_cursor_payload(f"fit|{raw_score}|123".encode())
        with pytest.raises(ValueError, match="Invalid cursor"):
            await repo.list_matches(user_id=1, limit=2, cursor=cursor, ranking_mode="fit")


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
