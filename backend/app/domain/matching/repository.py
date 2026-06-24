"""Matching repository: vector ranking + match-row persistence (Phase 3)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.resume import CandidateProfile, Resume
from app.models.user_job_match import UserJobMatch


class MatchingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_profile(self, user_id: int) -> CandidateProfile | None:
        """Return the user's active candidate profile (with embedding)."""
        result = await self._session.execute(
            select(CandidateProfile)
            .join(Resume, Resume.id == CandidateProfile.resume_id)
            .where(
                CandidateProfile.user_id == user_id,
                Resume.is_active.is_(True),
            )
            .order_by(CandidateProfile.updated_at.desc())
        )
        return result.scalars().first()

    async def rank_jobs_by_vector(
        self, embedding: list[float], limit: int
    ) -> list[tuple[int, float]]:
        """Return ``(job_id, similarity)`` ordered by cosine similarity desc."""
        distance = Job.description_embedding.cosine_distance(embedding)
        stmt = (
            select(Job.id, (1 - distance).label("similarity"))
            .where(Job.description_embedding.is_not(None))
            .order_by(distance)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [(int(row.id), float(row.similarity)) for row in result.all()]

    async def upsert_match(
        self,
        *,
        user_id: int,
        job_id: int,
        vector_score: float,
        confidence: str | None,
    ) -> None:
        stmt = pg_insert(UserJobMatch).values(
            user_id=user_id,
            job_id=job_id,
            vector_score=vector_score,
            confidence=confidence,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[UserJobMatch.user_id, UserJobMatch.job_id],
            set_={
                "vector_score": stmt.excluded.vector_score,
                "confidence": stmt.excluded.confidence,
                "updated_at": datetime.now(UTC),
            },
        )
        await self._session.execute(stmt)

    async def list_matches(
        self, *, user_id: int, limit: int, offset: int
    ) -> tuple[list[tuple[Job, UserJobMatch]], int]:
        base = (
            select(Job, UserJobMatch)
            .join(UserJobMatch, UserJobMatch.job_id == Job.id)
            .where(UserJobMatch.user_id == user_id)
        )
        count_stmt = (
            select(func.count())
            .select_from(UserJobMatch)
            .where(UserJobMatch.user_id == user_id)
        )
        total = int((await self._session.execute(count_stmt)).scalar_one())
        list_stmt = (
            base.order_by(UserJobMatch.vector_score.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(list_stmt)).all()
        return [(row[0], row[1]) for row in rows], total

    async def get_match(
        self, *, user_id: int, job_id: int
    ) -> UserJobMatch | None:
        result = await self._session.execute(
            select(UserJobMatch).where(
                UserJobMatch.user_id == user_id,
                UserJobMatch.job_id == job_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_job(self, job_id: int) -> Job | None:
        return await self._session.get(Job, job_id)

    async def save_explanation(
        self,
        *,
        match: UserJobMatch,
        llm_score: int | None,
        llm_confidence: float | None,
        explanation: str | None,
        matched_skills: list[str],
        missing_keywords: list[str],
        deterministic_match: dict[str, Any],
        confidence: str | None,
    ) -> UserJobMatch:
        match.llm_score = llm_score
        match.llm_confidence = llm_confidence
        match.llm_explanation = explanation
        match.matched_skills = matched_skills
        match.missing_keywords = missing_keywords
        match.deterministic_match = deterministic_match
        if confidence is not None:
            match.confidence = confidence
        await self._session.flush()
        return match
