"""Matching repository: vector ranking + match-row persistence (Phase 3)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.resume import CandidateProfile, Resume
from app.models.user_job import UserJob
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
        self, embedding: list[float], limit: int, *, user_id: int
    ) -> list[tuple[int, float]]:
        """Return ``(job_id, similarity)`` ordered by cosine similarity desc.

        Only includes jobs the user is allowed to see: public jobs + their own
        private imports.
        """
        distance = Job.description_embedding.cosine_distance(embedding)
        stmt = (
            select(Job.id, (1 - distance).label("similarity"))
            .where(
                Job.description_embedding.is_not(None),
                or_(
                    Job.visibility == "public",
                    Job.created_by_user_id == user_id,
                ),
            )
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
        self,
        *,
        user_id: int,
        limit: int,
        offset: int,
        q: str | None = None,
        source: str | None = None,
        is_remote: bool | None = None,
        job_type: str | None = None,
        location_contains: str | None = None,
        company_contains: str | None = None,
        shift_type: str | None = None,
        source_type: str | None = None,
    ) -> tuple[list[tuple[Job, UserJobMatch]], int]:
        """Return ranked matches with optional filters on job fields.

        Imported jobs without match scores are excluded; they belong in
        /imported, not /matches, until analysis completes.
        """
        filters: list[Any] = [
            UserJobMatch.user_id == user_id,
            Job.status == "active",
        ]

        if q:
            like = f"%{q}%"
            filters.append(
                or_(
                    Job.title.ilike(like),
                    Job.company.ilike(like),
                    Job.description.ilike(like),
                )
            )
        if source is not None:
            filters.append(Job.source == source)
        if is_remote is not None:
            filters.append(Job.is_remote.is_(is_remote))
        if shift_type is not None:
            filters.append(Job.shift_type == shift_type)
        if source_type == "global":
            filters.append(Job.visibility == "public")
        elif source_type == "manual":
            filters.append(
                and_(Job.visibility == "private", Job.ingest_source == "manual")
            )
        elif source_type == "extension":
            filters.append(
                and_(Job.visibility == "private", Job.ingest_source == "extension")
            )
        if job_type is not None:
            filters.append(func.lower(Job.job_type) == job_type.lower())
        if location_contains is not None:
            filters.append(Job.location.ilike(f"%{location_contains}%"))
        if company_contains is not None:
            filters.append(Job.company.ilike(f"%{company_contains}%"))

        count_stmt = (
            select(func.count())
            .select_from(UserJobMatch)
            .join(Job, UserJobMatch.job_id == Job.id)
            .where(*filters)
        )
        total = int((await self._session.execute(count_stmt)).scalar_one())

        list_stmt = (
            select(Job, UserJobMatch)
            .join(UserJobMatch, UserJobMatch.job_id == Job.id)
            .where(*filters)
            .order_by(UserJobMatch.vector_score.desc(), Job.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(list_stmt)).all()
        return [(r[0], r[1]) for r in rows], total

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

    async def aggregate_missing_keywords(
        self, *, user_id: int, top_n: int
    ) -> list[tuple[str, int]]:
        """Return the most frequent missing keywords across the user's matches.

        Unnests the ``missing_keywords`` JSONB arrays and counts occurrences,
        normalising case so "React" and "react" collapse to one bucket.
        """
        keyword = func.lower(
            func.jsonb_array_elements_text(UserJobMatch.missing_keywords)
        ).label("keyword")
        subquery = (
            select(keyword)
            .where(
                UserJobMatch.user_id == user_id,
                UserJobMatch.missing_keywords.isnot(None),
            )
            .subquery()
        )
        stmt = (
            select(subquery.c.keyword, func.count().label("frequency"))
            .group_by(subquery.c.keyword)
            .order_by(func.count().desc())
            .limit(top_n)
        )
        result = await self._session.execute(stmt)
        return [(str(row.keyword), int(row.frequency)) for row in result.all()]

    async def count_matches_with_gaps(self, user_id: int) -> int:
        """Count the user's matches that carry an explained missing-keywords list."""
        stmt = (
            select(func.count())
            .select_from(UserJobMatch)
            .where(
                UserJobMatch.user_id == user_id,
                UserJobMatch.missing_keywords.isnot(None),
            )
        )
        return int((await self._session.execute(stmt)).scalar_one())

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
