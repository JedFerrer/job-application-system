"""Database regression tests for job embedding persistence."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.jobs.repository import JobRepository
from app.models.job import Job

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, poolclass=NullPool, echo=False)


@pytest.fixture(autouse=True)
async def clean_db(engine: AsyncEngine) -> AsyncIterator[None]:
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as db:
        await db.execute(text("TRUNCATE TABLE jobs CASCADE"))
        await db.commit()
    yield


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def create_job(db: AsyncSession, *, raw_response: dict[str, object]) -> Job:
    job = Job(
        fingerprint="embed-repo-test",
        title="Embedding Repository Test",
        company="Acme",
        location="Remote",
        source="test",
        source_url="https://example.com/embedding-repository-test",
        raw_response=raw_response,
        status="unknown",
    )
    db.add(job)
    await db.flush()
    return job


@pytest.mark.asyncio
async def test_set_embedding_persists_vector_clears_only_embed_error_and_does_not_commit(
    engine: AsyncEngine,
) -> None:
    factory = session_factory(engine)
    async with factory() as db:
        job = await create_job(
            db,
            raw_response={"source": {"id": "abc"}, "embed_error": "old failure"},
        )
        job_id = job.id
        await db.commit()

    async with factory() as db:
        embedding = [0.25] * get_settings().embedding_dim
        await JobRepository(db).set_embedding(job_id, embedding)
        await db.flush()
        stored = await db.scalar(select(Job).where(Job.id == job_id))

        assert stored is not None
        assert stored.description_embedding == embedding
        assert stored.status == "active"
        assert stored.raw_response == {"source": {"id": "abc"}}
        await db.rollback()

    async with factory() as db:
        rolled_back = await db.get(Job, job_id)
        assert rolled_back is not None
        assert rolled_back.description_embedding is None
        assert rolled_back.status == "unknown"
        assert rolled_back.raw_response["embed_error"] == "old failure"


@pytest.mark.asyncio
async def test_set_embed_error_persists_special_characters_and_preserves_metadata(
    engine: AsyncEngine,
) -> None:
    error = 'Provider said "retry"; it\'s unavailable\nUnicode: caf\u00e9 \u2603 {"detail": true}'
    factory = session_factory(engine)
    async with factory() as db:
        job = await create_job(db, raw_response={"source": {"id": "abc"}, "attempt": 3})
        job_id = job.id
        await db.commit()

    async with factory() as db:
        await JobRepository(db).set_embed_error(job_id, error)
        await db.flush()
        stored = await db.scalar(select(Job).where(Job.id == job_id))

        assert stored is not None
        assert stored.status == "unknown"
        assert stored.raw_response == {
            "source": {"id": "abc"},
            "attempt": 3,
            "embed_error": error,
        }
        await db.rollback()

    async with factory() as db:
        rolled_back = await db.get(Job, job_id)
        assert rolled_back is not None
        assert rolled_back.raw_response == {"source": {"id": "abc"}, "attempt": 3}
