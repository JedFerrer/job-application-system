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

MANUAL_NEXT_ACTION_EVENT_SOURCE = "browser_application_update"
MANUAL_NEXT_ACTION_EVENT_TYPES = {
    "created": "manual_next_action_created",
    "updated": "manual_next_action_updated",
    "completed": "manual_next_action_completed",
    "cleared": "manual_next_action_cleared",
}
NEXT_ACTION_FIELDS = (
    "next_action_type",
    "next_action_note",
    "next_action_due_date",
    "next_action_completed_at",
)


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

        previous_next_action = _next_action_snapshot(application)
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

        if any(field in fields_set for field in NEXT_ACTION_FIELDS):
            current_next_action = _next_action_snapshot(updated)
            event_type, changed_fields = _classify_manual_next_action_event(
                previous_next_action=previous_next_action,
                current_next_action=current_next_action,
                payload_fields=fields_set,
            )
            if event_type is not None:
                await self._repo.create_event(
                    user_id=user_id,
                    application_id=updated.id,
                    job_id=updated.job_id,
                    event_type=event_type,
                    metadata=_manual_next_action_metadata(
                        previous_next_action=previous_next_action,
                        current_next_action=current_next_action,
                        changed_fields=changed_fields,
                    ),
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


def _next_action_snapshot(
    application: object,
) -> dict[str, object | None]:
    note = getattr(application, "next_action_note", None)
    return {
        "type": getattr(application, "next_action_type", None),
        "note": note,
        "due_date": getattr(application, "next_action_due_date", None),
        "completed_at": getattr(application, "next_action_completed_at", None),
        "has_note": bool(note and str(note).strip()),
    }


def _has_next_action(snapshot: dict[str, object | None]) -> bool:
    return any(
        snapshot[field] is not None
        for field in ("type", "note", "due_date", "completed_at")
    )


def _classify_manual_next_action_event(
    *,
    previous_next_action: dict[str, object | None],
    current_next_action: dict[str, object | None],
    payload_fields: set[str],
) -> tuple[str | None, list[str]]:
    changed_fields = [
        field
        for field in NEXT_ACTION_FIELDS
        if previous_next_action[_snapshot_key(field)]
        != current_next_action[_snapshot_key(field)]
    ]
    if not changed_fields:
        return None, []

    previous_has_next_action = _has_next_action(previous_next_action)
    current_has_next_action = _has_next_action(current_next_action)
    previous_completed = previous_next_action["completed_at"] is not None
    current_completed = current_next_action["completed_at"] is not None

    if previous_has_next_action and not current_has_next_action:
        return MANUAL_NEXT_ACTION_EVENT_TYPES["cleared"], changed_fields
    if (
        "next_action_completed_at" in payload_fields
        and not previous_completed
        and current_completed
    ):
        return MANUAL_NEXT_ACTION_EVENT_TYPES["completed"], changed_fields
    if not previous_has_next_action and current_has_next_action:
        return MANUAL_NEXT_ACTION_EVENT_TYPES["created"], changed_fields
    return MANUAL_NEXT_ACTION_EVENT_TYPES["updated"], changed_fields


def _manual_next_action_metadata(
    *,
    previous_next_action: dict[str, object | None],
    current_next_action: dict[str, object | None],
    changed_fields: list[str],
) -> dict[str, object]:
    return {
        "source": MANUAL_NEXT_ACTION_EVENT_SOURCE,
        "previous": {
            "type": previous_next_action["type"],
            "due_date": _date_to_iso(previous_next_action["due_date"]),
            "completed": previous_next_action["completed_at"] is not None,
            "has_note": previous_next_action["has_note"],
        },
        "current": {
            "type": current_next_action["type"],
            "due_date": _date_to_iso(current_next_action["due_date"]),
            "completed": current_next_action["completed_at"] is not None,
            "has_note": current_next_action["has_note"],
        },
        "changed_fields": changed_fields,
    }


def _snapshot_key(field_name: str) -> str:
    if field_name == "next_action_type":
        return "type"
    if field_name == "next_action_note":
        return "note"
    if field_name == "next_action_due_date":
        return "due_date"
    if field_name == "next_action_completed_at":
        return "completed_at"
    raise KeyError(field_name)


def _date_to_iso(value: object | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
