"""Application event service — records and queries application lifecycle events.

Event types tracked:
  job_detected, job_imported, apply_clicked, ats_redirect_detected,
  form_detected, autofill_started, autofill_completed, submit_clicked,
  confirmation_detected, user_marked_applied
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select

from app.core.logging import get_logger
from app.domain.applications.attempts import ApplicationAttemptService
from app.domain.applications.preparation import ApplicationPreparationService
from app.domain.applications.repository import ApplicationRepository
from app.domain.extension.exceptions import EventIdConflictError, ExtensionSessionNotFoundError
from app.domain.extension.repository import ExtensionRepository
from app.domain.extension.schemas import (
    ApplicationEventItem,
    ApplicationEventRequest,
    ApplicationEventResponse,
    ApplicationTimelineResponse,
    MarkAppliedRequest,
    MarkAppliedResponse,
)
from app.domain.jobs.identity import canonicalize_job_url
from app.domain.jobs.repository import JobRepository
from app.models.application_attempt import ApplicationAttempt
from app.models.application_preparation import ApplicationPreparation

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
        event_id = str(request.event_id) if request.event_id else None
        existing = (
            await self._ext.get_event_by_id(user_id=user_id, event_id=event_id)
            if event_id else None
        )
        if existing is not None:
            if not self._same_event(existing, request):
                raise EventIdConflictError()
            return ApplicationEventResponse(
                application_id=str(existing.application_id or ""), job_id=existing.job_id or 0,
                status="saved", event_recorded=True, idempotent=True,
            )
        attempt_id = str(request.attempt_id) if request.attempt_id else None
        attempt = await self._attempt(user_id=user_id, attempt_id=attempt_id)
        job_id: int | None = None
        application_id: int | None = None
        status = "saved"

        application = None
        if attempt is not None:
            application_id = attempt.application_id
            app = await self._apps.get(user_id=user_id, application_id=application_id)
            if app is None:
                raise ExtensionSessionNotFoundError("Application attempt is unavailable")
            job_id, status = app.job_id, app.status or "saved"
            if request.event_type == "opened":
                if not request.page_url:
                    raise ExtensionSessionNotFoundError("Application attempt is unavailable")
                candidates = await ApplicationAttemptService(self._ext._session).resolve(
                    user_id=user_id, url=request.page_url
                )
                if not any(candidate["attempt_id"] == attempt.id for candidate in candidates):
                    raise ExtensionSessionNotFoundError("Application attempt is unavailable")

        # For apply_clicked, try to find or import the job first
        if attempt is None and request.event_type == "apply_clicked" and request.job_url:
            canonical = canonicalize_job_url(request.job_url)
            job = (
                None
                if canonical is None
                else await self._jobs.resolve_visible_by_url(
                    user_id=user_id, canonical_url=canonical
                )
            )

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
        recorded = await self._ext.create_event(
            user_id=user_id,
            event_type=request.event_type,
            application_id=application_id,
            job_id=job_id,
            source_url=_safe_url(request.job_url),
            page_url=_safe_url(request.page_url),
            metadata=_safe_metadata(request.metadata),
            attempt_id=attempt.id if attempt else None,
            event_id=event_id,
        )
        if event_id is not None and not self._same_event(recorded, request):
            raise EventIdConflictError()

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
        event_id = str(request.event_id) if request.event_id else None
        existing = (
            await self._ext.get_event_by_id(user_id=user_id, event_id=event_id)
            if event_id else None
        )
        if existing is not None:
            attempt_id = str(request.attempt_id) if request.attempt_id else None
            if (
                existing.event_type != request.confirmation_source
                or existing.attempt_id != attempt_id
                or existing.source_url != _safe_url(request.job_url)
                or existing.metadata_ != {"confirmation_source": request.confirmation_source}
            ):
                raise EventIdConflictError()
            return MarkAppliedResponse(
                application_id=str(existing.application_id), job_id=existing.job_id or 0,
                status="applied", marked_applied=True, idempotent=True,
            )
        attempt_id = str(request.attempt_id) if request.attempt_id else None
        attempt = await self._attempt(user_id=user_id, attempt_id=attempt_id)
        # Find the job by URL for legacy callers. A correlated attempt is authoritative.
        canonical = canonicalize_job_url(request.job_url)
        job = (
            None
            if canonical is None
            else await self._jobs.resolve_visible_by_url(
                user_id=user_id, canonical_url=canonical
            )
        )

        if attempt is not None:
            application = await self._apps.get(
                user_id=user_id, application_id=attempt.application_id
            )
            if application is None:
                raise ExtensionSessionNotFoundError("Application attempt is unavailable")
            job = await self._jobs.get_by_id(application.job_id)
        elif job is None:
            raise ExtensionSessionNotFoundError(
                f"No job found for URL: {request.job_url}"
            )
        if job is None:
            raise ExtensionSessionNotFoundError("Application attempt job is unavailable")

        # Find or create the application
        if attempt is None:
            application = await self._apps.create_or_get(user_id=user_id, job_id=job.id)
        assert application is not None

        reservation = await self._ext.reserve_event(
            user_id=user_id,
            event_type=request.confirmation_source,
            application_id=application.id,
            job_id=job.id,
            source_url=_safe_url(request.job_url),
            metadata={"confirmation_source": request.confirmation_source},
            attempt_id=attempt.id if attempt else None,
            event_id=event_id,
        )
        if not reservation.created:
            if (
                reservation.event.event_type != request.confirmation_source
                or reservation.event.attempt_id != (attempt.id if attempt else None)
                or reservation.event.application_id != application.id
                or reservation.event.job_id != job.id
                or reservation.event.source_url != _safe_url(request.job_url)
                or reservation.event.metadata_
                != {"confirmation_source": request.confirmation_source}
            ):
                raise EventIdConflictError()
            return MarkAppliedResponse(
                application_id=str(application.id), job_id=job.id,
                status=application.status or "saved", marked_applied=True, idempotent=True,
            )

        # Update status to applied (only if not already applied or further along)
        if application.status in ("saved",):
            application.status = "applied"

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
        self, *, application_id: int, user_id: int
    ) -> ApplicationTimelineResponse:
        """Compatibility projection for existing extension callers."""
        events = await self._ext.list_events_for_application(
            application_id=application_id, user_id=user_id
        )
        return ApplicationTimelineResponse(
            application_id=str(application_id),
            events=[
                ApplicationEventItem(
                    id=event.id,
                    event_type=event.event_type,
                    source_url=event.source_url,
                    page_url=event.page_url,
                    metadata=event.metadata_,
                    created_at=event.created_at,
                )
                for event in events
            ],
        )

    async def _attempt(self, *, user_id: int, attempt_id: str | None) -> ApplicationAttempt | None:
        if attempt_id is None:
            return None
        attempt = await self._ext._session.scalar(select(ApplicationAttempt).where(
            ApplicationAttempt.id == attempt_id, ApplicationAttempt.user_id == user_id,
            ApplicationAttempt.expires_at > datetime.now(UTC),
        ))
        if attempt is None:
            raise ExtensionSessionNotFoundError("Application attempt is unavailable")
        preparation = await self._ext._session.get(
            ApplicationPreparation, attempt.application_id
        )
        if preparation is None or preparation.approved_package_id != attempt.package_id:
            raise ExtensionSessionNotFoundError("Application attempt is stale")
        current = await ApplicationPreparationService(session=self._ext._session).get(
            user_id=user_id, application_id=attempt.application_id
        )
        if current.state != "ready" or not current.approval_current:
            raise ExtensionSessionNotFoundError("Application attempt is stale")
        return attempt

    @staticmethod
    def _same_event(existing: object, request: ApplicationEventRequest) -> bool:
        return (
            getattr(existing, "event_type", None) == request.event_type
            and getattr(existing, "attempt_id", None)
            == (str(request.attempt_id) if request.attempt_id else None)
            and getattr(existing, "source_url", None) == _safe_url(request.job_url)
            and getattr(existing, "page_url", None) == _safe_url(request.page_url)
            and getattr(existing, "metadata_", None) == _safe_metadata(request.metadata)
        )


def _safe_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        return urlunsplit(
            (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", "", "")
        )
    except ValueError:
        return None


def _safe_metadata(metadata: dict[str, object]) -> dict[str, object]:
    allowed = {"domain", "ats_type", "field_count", "attempted", "filled", "skipped", "failed"}
    return {
        key: value
        for key, value in metadata.items()
        if key in allowed and isinstance(value, (str, int, float, bool))
    }
