"""Application endpoints (Phase 4 minimal CRM)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Header, Query, Response, status

from app.api.deps import ApplicationPreparationServiceDep, ApplicationServiceDep, CurrentUser
from app.domain.applications.schemas import (
    ApplicationActionCenterResponse,
    ApplicationActivityResponse,
    ApplicationCreate,
    ApplicationDashboardSummary,
    ApplicationListResponse,
    ApplicationPackageResponse,
    ApplicationResponse,
    ApplicationUpdate,
    ApplicationWorkspaceResponse,
    ApprovePreparationRequest,
    CreatePackageRequest,
    PreparationPatch,
    PreparationResponse,
)

router = APIRouter(prefix="/applications", tags=["applications"])


@router.post("", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED)
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
    return await service.list(user_id=current_user.id, cursor=cursor, limit=limit)


@router.get("/dashboard-summary", response_model=ApplicationDashboardSummary)
async def get_dashboard_summary(
    current_user: CurrentUser,
    service: ApplicationServiceDep,
) -> ApplicationDashboardSummary:
    return await service.dashboard_summary(user_id=current_user.id)


@router.get("/next-actions", response_model=ApplicationListResponse)
async def list_due_next_actions(
    current_user: CurrentUser,
    service: ApplicationServiceDep,
    due_by: date = Query(...),
) -> ApplicationListResponse:
    return await service.list_due_next_actions(user_id=current_user.id, due_by=due_by)


@router.get("/action-center", response_model=ApplicationActionCenterResponse)
async def get_action_center(
    current_user: CurrentUser,
    service: ApplicationServiceDep,
    today: date = Query(...),
) -> ApplicationActionCenterResponse:
    return await service.action_center(user_id=current_user.id, today=today)


@router.get("/{application_id}/preparation", response_model=PreparationResponse)
async def get_preparation(
    application_id: int, current_user: CurrentUser, service: ApplicationPreparationServiceDep
) -> PreparationResponse:
    return await service.get(user_id=current_user.id, application_id=application_id)


@router.get("/{application_id}/workspace", response_model=ApplicationWorkspaceResponse)
async def get_application_workspace(
    application_id: int, current_user: CurrentUser, service: ApplicationServiceDep
) -> ApplicationWorkspaceResponse:
    return await service.workspace(user_id=current_user.id, application_id=application_id)


@router.patch("/{application_id}/preparation", response_model=PreparationResponse)
async def patch_preparation(
    application_id: int,
    payload: PreparationPatch,
    current_user: CurrentUser,
    service: ApplicationPreparationServiceDep,
) -> PreparationResponse:
    return await service.patch(
        user_id=current_user.id, application_id=application_id, payload=payload
    )


@router.post("/{application_id}/packages", response_model=ApplicationPackageResponse)
async def create_application_package(
    application_id: int,
    payload: CreatePackageRequest,
    response: Response,
    current_user: CurrentUser,
    service: ApplicationPreparationServiceDep,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
) -> ApplicationPackageResponse:
    package, created = await service.create_package(
        user_id=current_user.id,
        application_id=application_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return package


@router.get("/{application_id}/packages/{package_id}", response_model=ApplicationPackageResponse)
async def get_application_package(
    application_id: int,
    package_id: int,
    current_user: CurrentUser,
    service: ApplicationPreparationServiceDep,
) -> ApplicationPackageResponse:
    return await service.get_package(
        user_id=current_user.id, application_id=application_id, package_id=package_id
    )


@router.post("/{application_id}/preparation/approve", response_model=PreparationResponse)
async def approve_preparation(
    application_id: int,
    payload: ApprovePreparationRequest,
    current_user: CurrentUser,
    service: ApplicationPreparationServiceDep,
) -> PreparationResponse:
    return await service.approve(
        user_id=current_user.id, application_id=application_id, payload=payload
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


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_application(
    application_id: int,
    current_user: CurrentUser,
    service: ApplicationServiceDep,
    confirm_discard_preparation: bool = Query(default=False),
) -> None:
    await service.delete(
        user_id=current_user.id,
        application_id=application_id,
        confirm_discard_preparation=confirm_discard_preparation,
    )


@router.get("/{application_id}/activity", response_model=ApplicationActivityResponse)
async def list_application_activity(
    application_id: int,
    current_user: CurrentUser,
    service: ApplicationServiceDep,
    limit: int = Query(default=10, ge=1, le=25),
) -> ApplicationActivityResponse:
    return await service.list_activity(
        user_id=current_user.id,
        application_id=application_id,
        limit=limit,
    )
