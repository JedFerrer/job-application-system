"""Database regression coverage for full match-ranking refreshes."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import uuid4

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
from app.domain.matching.repository import MatchingRepository
from app.domain.matching.service import MatchingService
from app.models.job import Job
from app.models.resume import CandidateProfile, Resume, SourceResumeContent
from app.models.user import User
from app.models.user_job import UserJob
from app.models.user_job_match import UserJobMatch

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


@pytest.fixture(autouse=True)
async def clean_db(engine: AsyncEngine) -> AsyncGenerator[None]:
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        await db.execute(
            text(
                "TRUNCATE TABLE user_job_matches, user_jobs, candidate_profiles, "
                "resumes, jobs, users RESTART IDENTITY CASCADE"
            )
        )
        await db.commit()
    yield


async def test_full_refresh_keeps_only_visible_active_automatic_rows(engine: AsyncEngine) -> None:
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    embedding = [0.0] * get_settings().embedding_dim
    embedding[0] = 1.0
    run_id = uuid4().hex

    async with session_factory() as db:
        user = User(email=f"match-refresh-{run_id}@example.com", name="Match Tester")
        db.add(user)
        await db.flush()

        resume = Resume(
            user_id=user.id,
            filename="resume.txt",
            raw_text="Python engineer",
            is_active=True,
            lifecycle_status="active",
        )
        db.add(resume)
        await db.flush()
        db.add(
            SourceResumeContent(
                resume_id=resume.id,
                schema_version="test-v1",
                content={"summary": "Python engineer"},
                validation_status="verified",
                validation_errors=[],
            )
        )
        db.add(
            CandidateProfile(
                user_id=user.id,
                resume_id=resume.id,
                parsed={"skills": ["Python"], "work_history": []},
                summary_text="Python engineer",
                embedding=embedding,
            )
        )

        active_job = Job(
            fingerprint=f"active-{run_id}"[:16],
            title="Active candidate",
            company="Active Co",
            location="Remote",
            description="Python services",
            description_embedding=embedding,
            source="test",
            source_url=f"https://example.com/{run_id}/active",
            visibility="public",
            ingest_source="scraper",
            status="active",
            raw_response={},
        )
        manual_job = Job(
            fingerprint=f"manual-{run_id}"[:16],
            title="Manual analysis",
            company="Manual Co",
            location="Remote",
            description="Python services",
            description_embedding=embedding,
            source="test",
            source_url=f"https://example.com/{run_id}/manual",
            visibility="public",
            ingest_source="scraper",
            status="active",
            raw_response={},
        )
        archived_job = Job(
            fingerprint=f"archived-{run_id}"[:16],
            title="Archived active job",
            company="Archived Co",
            location="Remote",
            description="Python services",
            description_embedding=embedding,
            source="test",
            source_url=f"https://example.com/{run_id}/archived",
            visibility="public",
            ingest_source="scraper",
            status="active",
            raw_response={},
        )
        expired_job = Job(
            fingerprint=f"expired-{run_id}"[:16],
            title="Expired stale job",
            company="Expired Co",
            location="Remote",
            description="Python services",
            description_embedding=embedding,
            source="test",
            source_url=f"https://example.com/{run_id}/expired",
            visibility="public",
            ingest_source="scraper",
            status="expired",
            raw_response={},
        )
        db.add_all([active_job, manual_job, archived_job, expired_job])
        await db.flush()

        db.add_all(
            [
                UserJob(user_id=user.id, job_id=archived_job.id, status="archived"),
                UserJobMatch(
                    user_id=user.id,
                    job_id=manual_job.id,
                    vector_score=99.0,
                    confidence="strong",
                    origin="manual",
                ),
                UserJobMatch(
                    user_id=user.id,
                    job_id=expired_job.id,
                    vector_score=99.0,
                    confidence="strong",
                    origin="automatic",
                ),
            ]
        )
        await db.commit()

        service = MatchingService(MatchingRepository(db))
        response = await service.rank_jobs(user.id, limit=2)
        await db.commit()

        assert response.ranked == 1
        stored = (
            await db.execute(
                select(UserJobMatch).where(UserJobMatch.user_id == user.id)
            )
        ).scalars().all()
        automatic_ids = {match.job_id for match in stored if match.origin == "automatic"}
        assert automatic_ids == {active_job.id}
        assert any(
            match.job_id == manual_job.id and match.origin == "manual" for match in stored
        )

        visible = await service.list_matches(user_id=user.id, limit=10)
        assert visible.total == 2
        assert {item.job_id for item in visible.items} == {active_job.id, manual_job.id}
