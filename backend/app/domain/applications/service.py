"""Application service (Phase 4 minimal CRM)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.core.logging import get_logger
from app.domain.applications.exceptions import (
    ApplicationJobNotActionableError,
    ApplicationNotFoundError,
    InvalidApplicationStatusTransitionError,
)
from app.domain.applications.repository import ApplicationRepository
from app.domain.applications.schemas import (
    MANUAL_NEXT_ACTION_EVENT_CLEARED,
    MANUAL_NEXT_ACTION_EVENT_COMPLETED,
    MANUAL_NEXT_ACTION_EVENT_CREATED,
    MANUAL_NEXT_ACTION_EVENT_UPDATED,
    ApplicationActionCenterDatedGroup,
    ApplicationActionCenterDatedItem,
    ApplicationActionCenterMissingGroup,
    ApplicationActionCenterMissingItem,
    ApplicationActionCenterResponse,
    ApplicationActivityEvent,
    ApplicationActivityResponse,
    ApplicationDashboardSummary,
    ApplicationListItem,
    ApplicationListResponse,
    ApplicationResponse,
    ApplicationStatusCounts,
    ApplicationUpdate,
)
from app.domain.jobs.exceptions import JobNotFoundError
from app.domain.jobs.repository import JobRepository

logger = get_logger("application_service")
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
        self._require_actionable_job(job)
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
        await self._require_actionable_application_job(application.job_id)

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

    async def delete(self, *, user_id: int, application_id: int) -> None:
        application = await self._repo.get(
            user_id=user_id, application_id=application_id
        )
        if application is None:
            raise ApplicationNotFoundError(application_id)
        await self._require_actionable_application_job(application.job_id)
        if application.status != "saved":
            raise InvalidApplicationStatusTransitionError(
                current_status=application.status,
                requested_status="unsaved",
            )
        await self._repo.delete(application=application)

    @staticmethod
    def _require_actionable_job(job: object) -> None:
        if getattr(job, "status", None) != "active":
            raise ApplicationJobNotActionableError

    async def _require_actionable_application_job(self, job_id: int) -> None:
        job = await self._jobs.get_by_id(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        self._require_actionable_job(job)

    async def list_activity(
        self, *, user_id: int, application_id: int, limit: int = 10
    ) -> ApplicationActivityResponse:
        application = await self._repo.get(
            user_id=user_id, application_id=application_id
        )
        if application is None:
            raise ApplicationNotFoundError(application_id)

        events = await self._repo.list_manual_activity_events(
            user_id=user_id,
            application_id=application_id,
            limit=limit,
        )
        return ApplicationActivityResponse(
            application_id=application_id,
            events=[
                ApplicationActivityEvent(
                    id=event.id,
                    event_type=event.event_type,
                    metadata=event.metadata_,
                    created_at=event.created_at,
                )
                for event in events
            ],
        )

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
                company_logo=job.company_logo,
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

    async def dashboard_summary(self, *, user_id: int) -> ApplicationDashboardSummary:
        counts = await self._repo.get_dashboard_status_counts(user_id=user_id)
        total = counts["total"]
        offer_count = counts["offer"]
        return ApplicationDashboardSummary(
            total_applications=total,
            counts_by_status=ApplicationStatusCounts(
                saved=counts["saved"],
                applied=counts["applied"],
                interview=counts["interview"],
                offer=offer_count,
                rejected=counts["rejected"],
                closed=counts["closed"],
            ),
            active_pipeline_count=counts["saved"] + counts["applied"] + counts["interview"],
            offer_count=offer_count,
            offer_rate_percent=round(100 * offer_count / total) if total else 0,
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
                company_logo=job.company_logo,
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

    async def action_center(
        self, *, user_id: int, today: date
    ) -> ApplicationActionCenterResponse:
        dated_rows = await self._repo.list_action_center_dated(
            user_id=user_id, end_date=today + timedelta(days=7)
        )
        missing_rows = await self._repo.list_action_center_needs_next_action(
            user_id=user_id
        )

        dated_items = [
            ApplicationActionCenterDatedItem(
                application_id=app.id,
                job_id=job.id,
                job_title=job.title,
                company=job.company,
                application_status=app.status,
                next_action_type=app.next_action_type,
                next_action_note=app.next_action_note,
                next_action_due_date=app.next_action_due_date,
                updated_at=app.updated_at,
            )
            for app, job in dated_rows
            if app.next_action_due_date is not None
        ]
        overdue = sorted(
            (item for item in dated_items if item.next_action_due_date < today),
            key=lambda item: (
                item.next_action_due_date,
                -item.updated_at.timestamp(),
                -item.application_id,
            ),
        )
        due_today = sorted(
            (item for item in dated_items if item.next_action_due_date == today),
            key=lambda item: (-item.updated_at.timestamp(), -item.application_id),
        )
        upcoming = sorted(
            (
                item
                for item in dated_items
                if today < item.next_action_due_date <= today + timedelta(days=7)
            ),
            key=lambda item: (
                item.next_action_due_date,
                -item.updated_at.timestamp(),
                -item.application_id,
            ),
        )
        missing_items = sorted(
            (
                ApplicationActionCenterMissingItem(
                    application_id=app.id,
                    job_id=job.id,
                    job_title=job.title,
                    company=job.company,
                    application_status=app.status,
                    updated_at=app.updated_at,
                )
                for app, job in missing_rows
            ),
            key=lambda item: (-item.updated_at.timestamp(), -item.application_id),
        )

        return ApplicationActionCenterResponse(
            overdue=ApplicationActionCenterDatedGroup(count=len(overdue), items=overdue),
            due_today=ApplicationActionCenterDatedGroup(count=len(due_today), items=due_today),
            upcoming=ApplicationActionCenterDatedGroup(count=len(upcoming), items=upcoming),
            needs_next_action=ApplicationActionCenterMissingGroup(
                count=len(missing_items), items=missing_items
            ),
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
        return MANUAL_NEXT_ACTION_EVENT_CLEARED, changed_fields
    if (
        "next_action_completed_at" in payload_fields
        and not previous_completed
        and current_completed
    ):
        return MANUAL_NEXT_ACTION_EVENT_COMPLETED, changed_fields
    if not previous_has_next_action and current_has_next_action:
        return MANUAL_NEXT_ACTION_EVENT_CREATED, changed_fields
    return MANUAL_NEXT_ACTION_EVENT_UPDATED, changed_fields


def _manual_next_action_metadata(
    *,
    previous_next_action: dict[str, object | None],
    current_next_action: dict[str, object | None],
    changed_fields: list[str],
) -> dict[str, object]:
    return {
        "source": "browser_application_update",
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
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return None
