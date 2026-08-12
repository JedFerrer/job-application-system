"""Interview prep repository — cache row + job/profile lookups (Phase 2)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.resume.repository import ResumeRepository
from app.models.interview_prep import InterviewPrep
from app.models.job import Job
from app.models.resume import CandidateProfile


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

    async def get_visible_job(
        self, *, user_id: int, job_id: int
    ) -> Job | None:
        """Return *job_id* only if visible to *user_id*.

        Same visibility + archive-exclusion predicate as
        ``JobRepository.list_jobs``.
        """
        from sqlalchemy import or_

        from app.models.user_job import UserJob

        visibility_filter = or_(
            Job.visibility == "public",
            Job.created_by_user_id == user_id,
        )
        archived_subq = (
            select(UserJob.job_id)
            .where(
                UserJob.user_id == user_id,
                UserJob.status == "archived",
            )
            .scalar_subquery()
        )
        result = await self._session.execute(
            select(Job).where(
                Job.id == job_id,
                Job.status == "active",
                visibility_filter,
                Job.id.not_in(archived_subq),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_profile(self, user_id: int) -> CandidateProfile | None:
        """Return only a complete, supported active profile."""
        return await ResumeRepository(self._session).get_active_profile(user_id)
