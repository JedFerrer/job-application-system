"""Application event service — records and queries application lifecycle events.

Event types tracked:
  job_detected, job_imported, apply_clicked, ats_redirect_detected,
  form_detected, autofill_started, autofill_completed, submit_clicked,
  confirmation_detected, user_marked_applied
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.domain.applications.repository import ApplicationRepository
from app.domain.extension.exceptions import ExtensionSessionNotFoundError
from app.domain.extension.repository import ExtensionRepository
from app.domain.extension.schemas import (
    ApplicationEventItem,
    ApplicationEventRequest,
    ApplicationEventResponse,
    ApplicationTimelineResponse,
    MarkAppliedRequest,
    MarkAppliedResponse,
)
from app.domain.jobs.repository import JobRepository
from app.models.application import Application

logger = get_logger("application_events")


class ApplicationEventService:
    def __init__(
        self,
        *,
        extension_repository: ExtensionRepository,
        application_repository: ApplicationRepository,
        job_repository: JobRepository,
    ) -> None:
        self._ext = extension_repository
        self._apps = application_repository
        self._jobs = job_repository

    async def record_event(
        self,
        *,
        user_id: int,
        request: ApplicationEventRequest,
    ) -> ApplicationEventResponse:
        """Record an application lifecycle event.

        For apply_clicked: also imports the job if missing and creates/updates
        an application record (status stays 'saved').
        """
        job_id: int | None = None
        application_id: int | None = None
        status = "saved"

        # For apply_clicked, try to find or import the job first
        if request.event_type == "apply_clicked" and request.job_url:
            job = await self._jobs.find_by_source_url(request.job_url)

            if job is not None:
                job_id = job.id
            # If job doesn't exist, we can't import without full job data
            # The content script should import the job first before tracking apply clicks

        if job_id is not None:
            # Create or get application record
            application = await self._apps.create_or_get(
                user_id=user_id, job_id=job_id
            )
            application_id = application.id
            status = application.status or "saved"

        # Record the event
        await self._ext.create_event(
            user_id=user_id,
            event_type=request.event_type,
            application_id=application_id,
            job_id=job_id,
            source_url=request.job_url,
            page_url=request.page_url,
            metadata=request.metadata,
        )

        logger.info(
            "application.event_recorded",
            user_id=user_id,
            event_type=request.event_type,
            application_id=application_id,
            job_id=job_id,
        )

        return ApplicationEventResponse(
            application_id=str(application_id) if application_id else "",
            job_id=job_id or 0,
            status=status,
            event_recorded=True,
        )

    async def mark_applied(
        self,
        *,
        user_id: int,
        request: MarkAppliedRequest,
    ) -> MarkAppliedResponse:
        """Mark an application as 'applied' after confirmation.

        Only called after confirmation_detected or user_marked_applied.
        """
        # Find the job by URL
        job = await self._jobs.find_by_source_url(request.job_url)

        if job is None:
            raise ExtensionSessionNotFoundError(
                f"No job found for URL: {request.job_url}"
            )

        # Find or create the application
        application = await self._apps.create_or_get(
            user_id=user_id, job_id=job.id
        )

        # Update status to applied (only if not already applied or further along)
        if application.status in ("saved",):
            application.status = "applied"
            # Flush through the session that owns the application (if any)
            try:
                from sqlalchemy.orm import object_session
                session = object_session(application)
                if session:
                    await session.flush()
            except Exception:
                pass  # Application may be detached (e.g., in tests)

        # Record the confirmation event
        await self._ext.create_event(
            user_id=user_id,
            event_type=request.confirmation_source,
            application_id=application.id,
            job_id=job.id,
            source_url=request.job_url,
            metadata={
                "confirmation_source": request.confirmation_source,
                "confirmation_text": request.confirmation_text,
            },
        )

        logger.info(
            "application.marked_applied",
            user_id=user_id,
            application_id=application.id,
            source=request.confirmation_source,
        )

        return MarkAppliedResponse(
            application_id=str(application.id),
            job_id=job.id,
            status=application.status or "applied",
            marked_applied=True,
        )

    async def get_timeline(
        self,
        *,
        application_id: int,
        user_id: int,
    ) -> ApplicationTimelineResponse:
        """Get chronological event timeline for an application."""
        events = await self._ext.list_events_for_application(
            application_id=application_id,
            user_id=user_id,
        )

        items = [
            ApplicationEventItem(
                id=e.id,
                event_type=e.event_type,
                source_url=e.source_url,
                page_url=e.page_url,
                metadata=e.metadata_,
                created_at=e.created_at,
            )
            for e in events
        ]

        return ApplicationTimelineResponse(
            application_id=str(application_id),
            events=items,
        )
