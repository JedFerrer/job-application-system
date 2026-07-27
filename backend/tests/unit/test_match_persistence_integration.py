"""PostgreSQL-backed Phase 3B1 persistence and invalidation coverage."""

from __future__ import annotations

from datetime import UTC, datetime

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
from app.models.user import User
from app.models.user_job_match import UserJobMatch

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


@pytest.fixture(autouse=True)
async def clean_db(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await session.execute(
            text("TRUNCATE TABLE user_job_matches, jobs, users RESTART IDENTITY CASCADE")
        )
        await session.commit()


@pytest.mark.asyncio
async def test_postgres_upsert_clears_changed_deterministic_llm_state(
    engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await session.execute(
            insert(User).values(id=9101, email="phase3b1@example.com", name="Phase 3B1")
        )
        await session.execute(
            insert(Job).values(
                id=9101,
                fingerprint=job_fingerprint("Acme", "Python Engineer", "Remote"),
                title="Python Engineer",
                company="Acme",
                location="Remote",
                description="Python services",
                description_embedding=[0.0] * 1536,
                source="test",
                source_url="https://example.com/phase3b1",
                raw_response={},
                visibility="public",
                ingest_source="scraper",
                status="active",
            )
        )
        await session.execute(
            insert(UserJobMatch).values(
                user_id=9101,
                job_id=9101,
                vector_score=70.0,
                fit_score=60.0,
                ranking_score=60.0,
                score_components={"version": "fit-scorer-v1", "value": 60},
                source_profile_matching_hash="old-profile",
                job_description_hash="old-description",
                ontology_version="old-ontology",
                scorer_version="old-scorer",
                readiness_preferences_hash="old-preferences",
                llm_score=84,
                llm_confidence=0.9,
                llm_explanation="stale",
                llm_analyzed_at=datetime.now(UTC),
                llm_error="old error",
                llm_input_hash="old-llm-hash",
                matched_skills=["python"],
                missing_keywords=[],
                deterministic_match={"missing_skills": []},
            )
        )
        await session.commit()

        repository = MatchingRepository(session)
        await repository.upsert_match(
            user_id=9101,
            job_id=9101,
            vector_score=70.0,
            confidence="moderate",
            matched_skills=["python"],
            missing_keywords=[],
            deterministic_match={"missing_skills": []},
            fit_score=60.0,
            ranking_score=60.0,
            score_components={"version": "fit-scorer-v1", "value": 60},
            source_profile_matching_hash="old-profile",
            job_description_hash="old-description",
            ontology_version="old-ontology",
            scorer_version="old-scorer",
            readiness_preferences_hash="old-preferences",
            retrieval_metadata={
                "sources": ["vector"],
                "source_ranks": {"vector": 1},
            },
        )
        await session.commit()

        async with factory() as reader:
            unchanged = (
                await reader.execute(
                    select(UserJobMatch).where(
                        UserJobMatch.user_id == 9101,
                        UserJobMatch.job_id == 9101,
                    )
                )
            ).scalar_one()
            assert unchanged.llm_score == 84
            assert unchanged.llm_input_hash == "old-llm-hash"

        await repository.upsert_match(
            user_id=9101,
            job_id=9101,
            vector_score=70.0,
            confidence="moderate",
            matched_skills=["python", "fastapi"],
            missing_keywords=["sql"],
            deterministic_match={"missing_skills": ["sql"]},
            fit_score=62.5,
            ranking_score=62.5,
            score_components={"version": "fit-scorer-v1", "value": 62.5},
            source_profile_matching_hash="new-profile",
            job_description_hash="new-description",
            ontology_version="new-ontology",
            scorer_version="new-scorer",
            readiness_preferences_hash="new-preferences",
            retrieval_metadata={
                "sources": ["skill", "vector"],
                "source_ranks": {"skill": 2, "vector": 3},
            },
        )
        await session.commit()

    async with factory() as reader:
        row = (
            await reader.execute(
                select(UserJobMatch).where(
                    UserJobMatch.user_id == 9101,
                    UserJobMatch.job_id == 9101,
                )
            )
        ).scalar_one()

        assert row.vector_score == 70.0
        assert row.fit_score == 62.5
        assert row.ranking_score == 62.5
        assert row.matched_skills == ["fastapi", "python"]
        assert row.missing_keywords == ["sql"]
        assert row.source_profile_matching_hash == "new-profile"
        assert row.retrieval_metadata == {
            "sources": ["skill", "vector"],
            "source_ranks": {"skill": 2, "vector": 3},
        }
        assert row.llm_score is None
        assert row.llm_confidence is None
        assert row.llm_explanation is None
        assert row.llm_analyzed_at is None
        assert row.llm_error is None
        assert row.llm_input_hash is None
