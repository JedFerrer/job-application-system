"""Application repository (Phase 4 minimal CRM)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.application import Application
from app.models.job import Job


class ApplicationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_or_get(self, *, user_id: int, job_id: int) -> Application:
        """Idempotently create a ``saved`` application for ``(user, job)``.

        Uses ON CONFLICT so re-saving the same job returns the existing row
        instead of raising a unique-violation (never SELECT-then-INSERT).
        """
        stmt = pg_insert(Application).values(
            user_id=user_id, job_id=job_id, status="saved"
        )
        upsert: Any = stmt.on_conflict_do_update(
            index_elements=[Application.user_id, Application.job_id],
            set_={"updated_at": datetime.now(UTC)},
        ).returning(Application)
        result = await self._session.execute(upsert)
        application: Application = result.scalar_one()
        return application

    async def get(self, *, user_id: int, application_id: int) -> Application | None:
        result = await self._session.execute(
            select(Application).where(
                Application.id == application_id,
                Application.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def update(
        self,
        *,
        application: Application,
        status: str | None,
        cover_letter_id: int | None,
        notes: str | None,
        update_cover_letter: bool,
        update_notes: bool,
    ) -> Application:
        if status is not None:
            application.status = status
        if update_cover_letter:
            application.cover_letter_id = cover_letter_id
        if update_notes:
            application.notes = notes
        await self._session.flush()
        await self._session.refresh(application)
        return application

    async def list_with_jobs(
        self, *, user_id: int
    ) -> tuple[list[tuple[Application, Job]], int]:
        count = int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(Application)
                    .where(Application.user_id == user_id)
                )
            ).scalar_one()
        )
        rows = (
            await self._session.execute(
                select(Application, Job)
                .join(Job, Job.id == Application.job_id)
                .where(Application.user_id == user_id)
                .order_by(Application.updated_at.desc())
            )
        ).all()
        return [(row[0], row[1]) for row in rows], count
