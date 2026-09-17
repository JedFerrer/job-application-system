"""Extension application event tracking API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import (
    ApplicationAttemptServiceDep,
    ApplicationEventServiceDep,
    CurrentExtensionUser,
)
from app.domain.extension.schemas import (
    ApplicationEventRequest,
    ApplicationEventResponse,
    AttemptCandidate,
    AttemptResolutionResponse,
    MarkAppliedRequest,
    MarkAppliedResponse,
)

router = APIRouter(prefix="/extension/applications", tags=["extension-applications"])


@router.get("/attempts", response_model=AttemptResolutionResponse)
async def resolve_application_attempts(
    current_user: CurrentExtensionUser,
    service: ApplicationAttemptServiceDep,
    url: str = Query(..., min_length=1, max_length=2048),
) -> AttemptResolutionResponse:
    candidates = await service.resolve(user_id=current_user.id, url=url)
    return AttemptResolutionResponse(candidates=[AttemptCandidate(**item) for item in candidates])


@router.post("/events", response_model=ApplicationEventResponse)
async def record_application_event(
    payload: ApplicationEventRequest,
    current_user: CurrentExtensionUser,
    service: ApplicationEventServiceDep,
) -> ApplicationEventResponse:
    """Record an application lifecycle event.

    Supported event types: job_detected, job_imported, apply_clicked,
    ats_redirect_detected, form_detected, autofill_started, autofill_completed,
    submit_clicked, confirmation_detected, user_marked_applied.

    For apply_clicked events: the backend checks if the job exists and
    creates/updates an application record (status stays 'saved' — not 'applied').
    """
    return await service.record_event(
        user_id=current_user.id,
        request=payload,
    )


@router.post("/mark-applied", response_model=MarkAppliedResponse)
async def mark_application_applied(
    payload: MarkAppliedRequest,
    current_user: CurrentExtensionUser,
    service: ApplicationEventServiceDep,
) -> MarkAppliedResponse:
    """Mark an application as 'applied' after confirmation.

    Only call this after:
    - Confirmation page detection (confirmation_detected), or
    - Explicit user confirmation (user_marked_applied).

    Never mark an application as 'applied' just because the user clicked Apply.
    """
    return await service.mark_applied(
        user_id=current_user.id,
        request=payload,
    )
