"""Explicit readiness rules; separate from job-search preferences."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, ReadinessPreferenceServiceDep
from app.domain.matching.readiness_preferences import (
    ReadinessPreferencePayload,
    ReadinessPreferenceResponse,
)

router = APIRouter(prefix="/readiness-preferences", tags=["readiness-preferences"])


@router.get("", response_model=ReadinessPreferenceResponse)
async def get_readiness_preferences(
    current_user: CurrentUser,
    service: ReadinessPreferenceServiceDep,
) -> ReadinessPreferenceResponse:
    return await service.get(current_user.id)


@router.put("", response_model=ReadinessPreferenceResponse)
async def upsert_readiness_preferences(
    payload: ReadinessPreferencePayload,
    current_user: CurrentUser,
    service: ReadinessPreferenceServiceDep,
) -> ReadinessPreferenceResponse:
    return await service.upsert(current_user.id, payload)
