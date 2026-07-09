"""Extension autofill and form endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AutofillProfileServiceDep, CurrentExtensionUser
from app.domain.extension.schemas import (
    FieldMappingRequest,
    FieldMappingResponse,
    FormFillLogRequest,
    FormFillLogResponse,
)

router = APIRouter(prefix="/extension", tags=["extension-autofill"])


@router.get("/profile/autofill")
async def get_autofill_profile(
    current_user: CurrentExtensionUser,
    service: AutofillProfileServiceDep,
) -> dict[str, object]:
    """Get the user's profile fields for autofill.

    Returns structured personal, links, work, authorization, education,
    experience, and skills sections. Only returns fields suitable for
    form filling — never exposes raw resume text or sensitive data.
    """
    return await service.get_autofill_profile(user_id=current_user.id)


@router.post("/forms/map-fields", response_model=FieldMappingResponse)
async def map_form_fields(
    payload: FieldMappingRequest,
    _current_user: CurrentExtensionUser,
    service: AutofillProfileServiceDep,
) -> FieldMappingResponse:
    """Use LLM to map unknown form fields to profile paths.

    IMPORTANT: Only field metadata (labels, types, options) is sent to the LLM.
    User personal values are NEVER included in the request.
    """
    return await service.map_fields(request=payload)


@router.post("/forms/fill-log", response_model=FormFillLogResponse)
async def log_form_fill(
    payload: FormFillLogRequest,
    current_user: CurrentExtensionUser,
    service: AutofillProfileServiceDep,
) -> FormFillLogResponse:
    """Record a form fill session with per-field details."""
    return await service.log_fill(
        user_id=current_user.id,
        request=payload,
    )
