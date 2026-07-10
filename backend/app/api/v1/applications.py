"""Application endpoints (Phase 4 minimal CRM)."""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.deps import ApplicationServiceDep, CurrentUser
from app.domain.applications.schemas import (
    ApplicationCreate,
    ApplicationListResponse,
    ApplicationResponse,
    ApplicationUpdate,
)

router = APIRouter(prefix="/applications", tags=["applications"])


@router.post(
    "", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED
)
async def create_application(
    payload: ApplicationCreate,
    current_user: CurrentUser,
    service: ApplicationServiceDep,
) -> ApplicationResponse:
    return await service.create(user_id=current_user.id, job_id=payload.job_id)


@router.get("", response_model=ApplicationListResponse)
async def list_applications(
    current_user: CurrentUser,
    service: ApplicationServiceDep,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> ApplicationListResponse:
    return await service.list(
        user_id=current_user.id, cursor=cursor, limit=limit
    )


@router.patch("/{application_id}", response_model=ApplicationResponse)
async def update_application(
    application_id: int,
    payload: ApplicationUpdate,
    current_user: CurrentUser,
    service: ApplicationServiceDep,
) -> ApplicationResponse:
    return await service.update(
        user_id=current_user.id, application_id=application_id, payload=payload
    )
