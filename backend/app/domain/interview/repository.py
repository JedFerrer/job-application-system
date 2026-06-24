"""Interview prep repository — cache row + job/profile lookups (Phase 2)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.interview_prep import InterviewPrep
from app.models.job import Job
from app.models.resume import CandidateProfile, Resume


class InterviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_prep(self, *, user_id: int, job_id: int) -> InterviewPrep | None:
        result = await self._session.execute(
            select(InterviewPrep).where(
                InterviewPrep.user_id == user_id,
                InterviewPrep.job_id == job_id,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_prep(
        self,
        *,
        user_id: int,
        job_id: int,
        questions: list[dict[str, object]],
        focus_areas: list[str],
        model: str | None,
    ) -> InterviewPrep:
        insert_stmt = pg_insert(InterviewPrep).values(
            user_id=user_id,
            job_id=job_id,
            questions=questions,
            focus_areas=focus_areas,
            model=model,
        )
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[InterviewPrep.user_id, InterviewPrep.job_id],
            set_={
                "questions": insert_stmt.excluded.questions,
                "focus_areas": insert_stmt.excluded.focus_areas,
                "model": insert_stmt.excluded.model,
                "updated_at": datetime.now(UTC),
            },
        ).returning(InterviewPrep)
        result = await self._session.execute(upsert_stmt)
        prep: InterviewPrep = result.scalar_one()
        return prep

    async def get_job(self, job_id: int) -> Job | None:
        return await self._session.get(Job, job_id)

    async def get_active_profile(self, user_id: int) -> CandidateProfile | None:
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
