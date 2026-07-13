"""Application service (Phase 4 minimal CRM)."""

from __future__ import annotations

from datetime import date

from app.core.logging import get_logger
from app.domain.applications.exceptions import ApplicationNotFoundError
from app.domain.applications.repository import ApplicationRepository
from app.domain.applications.schemas import (
    ApplicationListItem,
    ApplicationListResponse,
    ApplicationResponse,
    ApplicationUpdate,
)
from app.domain.jobs.exceptions import JobNotFoundError
from app.domain.jobs.repository import JobRepository

logger = get_logger("application_service")


class ApplicationService:
    def __init__(
        self,
        *,
        repository: ApplicationRepository,
        job_repository: JobRepository,
    ) -> None:
        self._repo = repository
        self._jobs = job_repository

    async def create(self, *, user_id: int, job_id: int) -> ApplicationResponse:
        job = await self._jobs.get_visible_by_id(user_id=user_id, job_id=job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        application = await self._repo.create_or_get(user_id=user_id, job_id=job_id)
        logger.info("application.created", user_id=user_id, job_id=job_id)
        return ApplicationResponse.model_validate(application)

    async def update(
        self, *, user_id: int, application_id: int, payload: ApplicationUpdate
    ) -> ApplicationResponse:
        application = await self._repo.get(
            user_id=user_id, application_id=application_id
        )
        if application is None:
            raise ApplicationNotFoundError(application_id)

        fields_set = payload.model_fields_set
        updated = await self._repo.update(
            application=application,
            status=payload.status,
            cover_letter_id=payload.cover_letter_id,
            notes=payload.notes,
            next_action_type=payload.next_action_type,
            next_action_note=payload.next_action_note,
            next_action_due_date=payload.next_action_due_date,
            next_action_completed_at=payload.next_action_completed_at,
            update_cover_letter="cover_letter_id" in fields_set,
            update_notes="notes" in fields_set,
            update_next_action_type="next_action_type" in fields_set,
            update_next_action_note="next_action_note" in fields_set,
            update_next_action_due_date="next_action_due_date" in fields_set,
            update_next_action_completed_at="next_action_completed_at"
            in fields_set,
        )
        return ApplicationResponse.model_validate(updated)

    async def list(
        self, *, user_id: int, cursor: str | None = None, limit: int = 50
    ) -> ApplicationListResponse:
        rows, total, next_cursor = await self._repo.list_with_jobs(
            user_id=user_id, cursor=cursor, limit=limit
        )
        items = [
            ApplicationListItem(
                id=app.id,
                job_id=job.id,
                job_title=job.title,
                company=job.company,
                location=job.location,
                source_url=job.source_url,
                status=app.status,
                cover_letter_id=app.cover_letter_id,
                notes=app.notes,
                next_action_type=app.next_action_type,
                next_action_note=app.next_action_note,
                next_action_due_date=app.next_action_due_date,
                next_action_completed_at=app.next_action_completed_at,
                created_at=app.created_at,
                updated_at=app.updated_at,
            )
            for app, job in rows
        ]
        return ApplicationListResponse(
            items=items, total=total, limit=limit, next_cursor=next_cursor
        )

    async def list_due_next_actions(
        self, *, user_id: int, due_by: date
    ) -> ApplicationListResponse:
        rows, total = await self._repo.list_due_next_actions(
            user_id=user_id, due_by=due_by
        )
        items = [
            ApplicationListItem(
                id=app.id,
                job_id=job.id,
                job_title=job.title,
                company=job.company,
                location=job.location,
                source_url=job.source_url,
                status=app.status,
                cover_letter_id=app.cover_letter_id,
                notes=app.notes,
                next_action_type=app.next_action_type,
                next_action_note=app.next_action_note,
                next_action_due_date=app.next_action_due_date,
                next_action_completed_at=app.next_action_completed_at,
                created_at=app.created_at,
                updated_at=app.updated_at,
            )
            for app, job in rows
        ]
        return ApplicationListResponse(
            items=items, total=total, limit=total, next_cursor=None
        )
