"""Preference endpoints: CRUD + on-demand scrape refresh (Phase 2)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, PreferenceServiceDep
from app.core.logging import get_logger
from app.domain.preferences.schemas import (
    PreferenceCreate,
    PreferenceResponse,
    PreferenceUpdate,
    RefreshResponse,
)

router = APIRouter(prefix="/preferences", tags=["preferences"])
logger = get_logger(__name__)


@router.get("", response_model=PreferenceResponse)
async def get_preferences(
    current_user: CurrentUser,
    service: PreferenceServiceDep,
) -> PreferenceResponse:
    return await service.get_preferences(current_user.id)


@router.put("", response_model=PreferenceResponse)
async def upsert_preferences(
    payload: PreferenceCreate,
    current_user: CurrentUser,
    service: PreferenceServiceDep,
) -> PreferenceResponse:
    """Create or fully replace the user's job preferences."""
    return await service.upsert_preferences(current_user.id, payload)


@router.patch("", response_model=PreferenceResponse)
async def patch_preferences(
    payload: PreferenceUpdate,
    current_user: CurrentUser,
    service: PreferenceServiceDep,
) -> PreferenceResponse:
    """Partially update the user's job preferences."""
    return await service.patch_preferences(current_user.id, payload)


@router.post(
    "/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RefreshResponse,
)
async def refresh_preferences(
    current_user: CurrentUser,
    service: PreferenceServiceDep,
) -> RefreshResponse:
    """Trigger an on-demand scrape for the user's preferences.

    Gated by a cooldown (returns 429 with ``Retry-After`` semantics via the
    ``RefreshCooldownError`` handler). On success the run is stamped immediately
    and a Celery task is dispatched.
    """
    await service.prepare_refresh(current_user.id)
    # Import locally so the API process doesn't hard-depend on the worker module
    # graph at import time.
    from app.workers.job_ingestion import scrape_all_active_preferences  # noqa: PLC0415

    async_result = scrape_all_active_preferences.delay(current_user.id, force=True)
    logger.info(
        "preferences.refresh.dispatched",
        user_id=current_user.id,
        task_id=async_result.id,
    )
    return RefreshResponse(
        task_id=async_result.id,
        dispatched_at=datetime.now(UTC),
    )
