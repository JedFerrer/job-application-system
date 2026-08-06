"""Authenticated dashboard aggregate endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.domain.dashboard.search_readiness import (
    SearchReadinessResponse,
    SearchReadinessService,
)
from app.domain.preferences.repository import PreferenceRepository
from app.domain.resume.repository import ResumeRepository

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/search-readiness", response_model=SearchReadinessResponse)
async def get_search_readiness(
    current_user: CurrentUser,
    db: DbSession,
) -> SearchReadinessResponse:
    try:
        return await SearchReadinessService(
            ResumeRepository(db), PreferenceRepository(db)
        ).get(current_user.id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Search readiness is temporarily unavailable.",
        ) from exc
