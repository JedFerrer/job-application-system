# ruff: noqa: E501, I001, A002
"""Application endpoints (Phase 4 minimal CRM)."""

from __future__ import annotations

from datetime import date

from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Response, UploadFile, status

from app.api.deps import (
    ApplicationAttemptServiceDep,
    ArtifactLifecycleServiceDep,
    ApplicationPreparationServiceDep,
    DbSession,
    DocumentReviewServiceDep,
    TaskStoreDep,
    ApplicationServiceDep,
    CurrentUser,
)
from app.domain.applications.schemas import (
    ArtifactDetail,
    ArtifactUploadResponse,
    ApplicationActionCenterResponse,
    ApplicationActivityResponse,
    ApplicationCreate,
    ApplicationDashboardSummary,
    ApplicationListResponse,
    ApplicationPackageResponse,
    ApplicationResponse,
    ApplicationTimelinePage,
    ApplicationUpdate,
    ApplicationWorkspaceResponse,
    ApprovePreparationRequest,
    AttemptCreateRequest,
    AttemptResponse,
    CreatePackageRequest,
    DocumentReviewQueuedResponse,
    DocumentReviewRequest,
    DocumentReviewResponse,
    PreparationPatch,
    PreparationResponse,
    ReviewAcknowledgmentRequest,
)
from app.domain.applications.document_review import ReviewConflictError, ReviewNotFoundError, run_document_review_background
from app.domain.applications.artifact_lifecycle import ArtifactConflictError, ArtifactNotFoundError, ArtifactTaskAdmissionError, run_artifact_validation_background
from app.domain.applications.artifacts import MAX_ARTIFACT_BYTES, ArtifactFailureCategory, ArtifactValidationError, canonical_filename

router = APIRouter(prefix="/applications", tags=["applications"])


def _artifact_http_error(exc: ArtifactValidationError) -> HTTPException:
    if exc.category in {ArtifactFailureCategory.SIZE_EXCEEDED, ArtifactFailureCategory.QUOTA_EXCEEDED}:
        return HTTPException(status_code=413, detail=exc.category.value)
    if exc.category == ArtifactFailureCategory.UNSUPPORTED_TYPE:
        return HTTPException(status_code=415, detail=exc.category.value)
    return HTTPException(status_code=422, detail=exc.category.value)


@router.post("/{application_id}/packages/{package_id}/artifacts", response_model=ArtifactUploadResponse)
async def upload_application_artifact(
    application_id: int, package_id: int, current_user: CurrentUser,
    service: ArtifactLifecycleServiceDep, task_store: TaskStoreDep, db: DbSession,
    background_tasks: BackgroundTasks, response: Response,
    file: UploadFile = File(...), document_type: str = Form(...), format: str = Form(...),
    exporter_version: str = Form(..., min_length=1, max_length=64),
) -> ArtifactUploadResponse:
    try:
        await service.owned_package(user_id=current_user.id, application_id=application_id, package_id=package_id)
        if document_type not in {"resume", "cover_letter"} or format not in {"pdf", "docx"}:
            raise HTTPException(status_code=415, detail="unsupported_type")
        content = bytearray()
        while len(content) <= MAX_ARTIFACT_BYTES:
            chunk = await file.read(min(64 * 1024, MAX_ARTIFACT_BYTES + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        if len(content) > MAX_ARTIFACT_BYTES:
            raise HTTPException(status_code=413, detail="size_exceeded")
        if not content:
            raise HTTPException(status_code=422, detail="empty_file")
        result, created = await service.create(
            user_id=current_user.id, application_id=application_id, package_id=package_id,
            document_type=document_type, format=format, filename=file.filename or document_type,
            content=bytes(content), exporter_version=exporter_version, task_store=task_store,
        )
        if created:
            await db.commit()
            artifact = result.artifact
            assert artifact.task_id is not None
            background_tasks.add_task(
                run_artifact_validation_background, artifact_id=artifact.id,
                task_id=artifact.task_id, validation_attempt=1,
            )
            response.status_code = status.HTTP_202_ACCEPTED
        else:
            response.status_code = status.HTTP_200_OK
        return result
    except ArtifactNotFoundError:
        raise HTTPException(status_code=404, detail="Artifact package not found") from None
    except ArtifactValidationError as exc:
        raise _artifact_http_error(exc) from None
    except ArtifactTaskAdmissionError:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Artifact validation temporarily unavailable") from None
    except HTTPException:
        raise
    except Exception:
        await db.rollback()
        raise
    finally:
        await file.close()


@router.get("/{application_id}/artifacts/{artifact_id}", response_model=ArtifactDetail)
async def get_application_artifact(
    application_id: int, artifact_id: int, current_user: CurrentUser, service: ArtifactLifecycleServiceDep,
) -> ArtifactDetail:
    try:
        return await service.detail(user_id=current_user.id, application_id=application_id, artifact_id=artifact_id)
    except ArtifactNotFoundError:
        raise HTTPException(status_code=404, detail="Artifact not found") from None


@router.get("/{application_id}/artifacts/{artifact_id}/download")
async def download_application_artifact(
    application_id: int, artifact_id: int, current_user: CurrentUser, service: ArtifactLifecycleServiceDep,
) -> Response:
    try:
        row = await service.owned_artifact(user_id=current_user.id, application_id=application_id, artifact_id=artifact_id)
    except ArtifactNotFoundError:
        raise HTTPException(status_code=404, detail="Artifact not found") from None
    filename = canonical_filename(row.filename, row.document_type, row.format)  # type: ignore[arg-type]
    return Response(
        content=row.content, media_type=row.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/{application_id}/artifacts/{artifact_id}/retry", response_model=ArtifactUploadResponse, status_code=202)
async def retry_application_artifact(
    application_id: int, artifact_id: int, current_user: CurrentUser,
    service: ArtifactLifecycleServiceDep, task_store: TaskStoreDep, db: DbSession,
    background_tasks: BackgroundTasks,
) -> ArtifactUploadResponse:
    try:
        result, attempt = await service.retry(user_id=current_user.id, application_id=application_id, artifact_id=artifact_id, task_store=task_store)
        await db.commit()
        assert result.artifact.task_id is not None
        background_tasks.add_task(
            run_artifact_validation_background, artifact_id=artifact_id,
            task_id=result.artifact.task_id,
            validation_attempt=attempt,
        )
        return result
    except ArtifactNotFoundError:
        raise HTTPException(status_code=404, detail="Artifact not found") from None
    except ArtifactConflictError:
        raise HTTPException(status_code=409, detail="Artifact is not eligible for retry") from None
    except ArtifactTaskAdmissionError:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Artifact validation temporarily unavailable") from None
    except Exception:
        await db.rollback()
        raise


@router.delete("/{application_id}/artifacts/{artifact_id}", status_code=204)
async def delete_application_artifact(
    application_id: int, artifact_id: int, current_user: CurrentUser,
    service: ArtifactLifecycleServiceDep, db: DbSession,
) -> Response:
    try:
        await service.delete(user_id=current_user.id, application_id=application_id, artifact_id=artifact_id)
        await db.commit()
        return Response(status_code=204)
    except ArtifactNotFoundError:
        raise HTTPException(status_code=404, detail="Artifact not found") from None
    except ArtifactConflictError:
        raise HTTPException(status_code=409, detail="Artifact is still validating") from None


@router.get("/tasks/{task_id}")
async def get_application_task(task_id: str, current_user: CurrentUser, task_store: TaskStoreDep) -> dict[str, Any]:
    task = await task_store.get(task_id)
    if task is None or task.get("user_id") != current_user.id:
        raise HTTPException(status_code=404, detail="Task not found or expired")
    return task


@router.post("/{application_id}/packages/{package_id}/reviews", response_model=DocumentReviewResponse | DocumentReviewQueuedResponse)
async def create_document_review(application_id: int, package_id: int, payload: DocumentReviewRequest, current_user: CurrentUser, service: DocumentReviewServiceDep, task_store: TaskStoreDep, db: DbSession, background_tasks: BackgroundTasks, response: Response, idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128)) -> DocumentReviewResponse | DocumentReviewQueuedResponse:
    try:
        result = await service.start(user_id=current_user.id, application_id=application_id, package_id=package_id, idempotency_key=idempotency_key, rerun=payload.rerun, task_store=task_store)
    except ReviewNotFoundError:
        raise HTTPException(status_code=404, detail="Package not found") from None
    except ReviewConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if isinstance(result, DocumentReviewQueuedResponse):
        response.status_code = status.HTTP_202_ACCEPTED
        if not result.existing:
            await db.commit()
            background_tasks.add_task(run_document_review_background, task_id=result.task_id, review_id=result.review_id, user_id=current_user.id)
    return result


@router.get("/{application_id}/packages/{package_id}/reviews/{review_id}", response_model=DocumentReviewResponse)
async def get_document_review(application_id: int, package_id: int, review_id: str, current_user: CurrentUser, service: DocumentReviewServiceDep) -> DocumentReviewResponse:
    try:
        return await service.get(user_id=current_user.id, application_id=application_id, package_id=package_id, review_id=review_id)
    except ReviewNotFoundError:
        raise HTTPException(status_code=404, detail="Review not found") from None


@router.post("/{application_id}/packages/{package_id}/reviews/{review_id}/acknowledgments", response_model=DocumentReviewResponse)
async def acknowledge_document_review(application_id: int, package_id: int, review_id: str, payload: ReviewAcknowledgmentRequest, current_user: CurrentUser, service: DocumentReviewServiceDep) -> DocumentReviewResponse:
    try:
        return await service.acknowledge(user_id=current_user.id, application_id=application_id, package_id=package_id, review_id=review_id, payload=payload)
    except ReviewNotFoundError:
        raise HTTPException(status_code=404, detail="Review not found") from None
    except ReviewConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/{application_id}/timeline", response_model=ApplicationTimelinePage)
async def get_application_timeline(
    application_id: int,
    current_user: CurrentUser,
    service: ApplicationServiceDep,
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
) -> ApplicationTimelinePage:
    return await service.timeline(
        user_id=current_user.id, application_id=application_id,
        limit=limit, cursor=cursor,
    )


@router.post(
    "/{application_id}/attempts", response_model=AttemptResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_application_attempt(
    application_id: int,
    payload: AttemptCreateRequest,
    current_user: CurrentUser,
    service: ApplicationAttemptServiceDep,
) -> AttemptResponse:
    return await service.create(
        user_id=current_user.id, application_id=application_id, request=payload
    )


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
