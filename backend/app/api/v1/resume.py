"""Resume endpoints: upload (async 202 + polling) + profile read/edit."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile, status
from fastapi.responses import Response

from app.api.deps import CurrentUser, DbSession, JobRepositoryDep, ResumeServiceDep, TaskStoreDep
from app.core.logging import get_logger
from app.domain.matching.ranking_dispatch import RankingDispatcher
from app.domain.resume.exceptions import (
    ProfileNotFoundError,
    ResumeParseError,
    ResumeSourceLayoutUnavailableError,
    ResumeTextTooLongError,
    TailoredResumeSnapshotInvalidError,
    UnsupportedResumeFormatError,
)
from app.domain.resume.repository import ResumeRepository
from app.domain.resume.schemas import (
    ProfileResponse,
    ProfileUpdate,
    ResumeImportDetail,
    ResumeImportListItem,
    TailoredResume,
    TailoredResumeDraftCreate,
    TailoredResumeDraftListItem,
    TailoredResumeDraftResponse,
)
from app.infrastructure.database import async_session_factory
from app.infrastructure.redis_client import get_redis
from app.infrastructure.resume_parser import (
    MAX_RESUME_SIZE_BYTES,
    SUPPORTED_EXTENSIONS,
)
from app.infrastructure.task_store import TaskStore

router = APIRouter(prefix="/resume", tags=["resume"])
logger = get_logger(__name__)

_ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


async def _enqueue_match_ranking(user_id: int) -> None:
    """Coalesce a post-profile refresh after the transaction commits."""
    await RankingDispatcher(get_redis(), TaskStore(get_redis())).schedule_profile_refresh(user_id)


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

async def _run_resume_bg(
    task_id: str, user_id: int, filename: str, content: bytes
) -> None:
    """Runs after 202 is returned: parse text, call LLM, embed, persist."""
    store = TaskStore(get_redis())

    async def _progress(**kw: Any) -> None:
        await store.update(task_id, **kw)

    try:
        async with async_session_factory() as db:
            try:
                # Local import avoids a module cycle.
                from app.domain.resume.service import ResumeService  # noqa: PLC0415

                service = ResumeService(ResumeRepository(db))
                result = await service.upload_and_parse(
                    user_id=user_id,
                    filename=filename,
                    content=content,
                    progress=_progress,
                )
                await db.commit()
                if result.matching_profile_changed:
                    await _enqueue_match_ranking(user_id)
            except (
                UnsupportedResumeFormatError,
                ResumeParseError,
                ResumeTextTooLongError,
            ):
                await db.rollback()
                raise
            except Exception:
                await db.rollback()
                raise

        await store.update(
            task_id,
            status="done",
            message=(
                f'Parsed "{filename}" and saved as a draft awaiting review'
                if not result.is_active
                else f'Parsed "{filename}"'
            ),
            result=result.model_dump(mode="json"),
        )
    except UnsupportedResumeFormatError as exc:
        await store.update(task_id, status="failed", error=exc.detail, message=exc.detail)
    except ResumeTextTooLongError as exc:
        await store.update(task_id, status="failed", error=str(exc), message=str(exc))
    except ResumeParseError as exc:
        logger.warning(
            "bg_resume.parse_failed",
            task_id=task_id,
            failure_stage=("source_verification" if "source" in exc.detail.lower() else "parse"),
        )
        msg = (
            "We couldn't verify the résumé text extracted from this file. "
            "Please try again or use another PDF/DOCX."
            if "source" in exc.detail.lower()
            else "We couldn't parse this résumé. Please try again or use another PDF/DOCX."
        )
        await store.update(task_id, status="failed", error=msg, message=msg)
    except Exception as exc:
        logger.exception("bg_resume.failed", task_id=task_id)
        await store.update(
            task_id, status="failed", error=str(exc), message=f"Failed: {exc}"
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload_resume(
    current_user: CurrentUser,
    task_store: TaskStoreDep,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> dict[str, str]:
    """Queue resume parsing.  Poll ``GET /resume/tasks/{task_id}`` for progress."""
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported file type. Upload a PDF or DOCX.",
        )
    if file.content_type and file.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="File content type does not match a PDF or DOCX.",
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file is empty.",
        )
    if len(content) > MAX_RESUME_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Resume exceeds the 10 MB size limit.",
        )

    task_id = await task_store.create("resume_parse", user_id=current_user.id)
    background_tasks.add_task(_run_resume_bg, task_id, current_user.id, filename, content)
    return {"task_id": task_id}


@router.get("/tasks/{task_id}")
async def get_resume_task(
    task_id: str,
    current_user: CurrentUser,
    task_store: TaskStoreDep,
) -> dict[str, Any]:
    """Poll resume-parse task progress.  Expires after 1 hour."""
    task = await task_store.get(task_id)
    if task is None or task.get("user_id") != current_user.id:
        raise HTTPException(status_code=404, detail="Task not found or expired")
    return task


@router.get("/profile", response_model=ProfileResponse | None)
async def get_profile(
    current_user: CurrentUser,
    service: ResumeServiceDep,
) -> ProfileResponse | None:
    try:
        return await service.get_profile(current_user.id)
    except ProfileNotFoundError:
        return None


@router.get("/imports", response_model=list[ResumeImportListItem])
async def list_resume_imports(
    current_user: CurrentUser,
    service: ResumeServiceDep,
) -> list[ResumeImportListItem]:
    return await service.list_import_drafts(current_user.id)


@router.get("/imports/{resume_id}", response_model=ResumeImportDetail)
async def get_resume_import(
    resume_id: int,
    current_user: CurrentUser,
    service: ResumeServiceDep,
) -> ResumeImportDetail:
    return await service.get_import_draft(
        user_id=current_user.id, resume_id=resume_id
    )


@router.post("/imports/{resume_id}/approve", response_model=ProfileResponse)
async def approve_resume_import(
    resume_id: int,
    current_user: CurrentUser,
    service: ResumeServiceDep,
    db: DbSession,
    background_tasks: BackgroundTasks,
) -> ProfileResponse:
    response = await service.approve_import_draft(
        user_id=current_user.id, resume_id=resume_id
    )
    await db.commit()
    if response.matching_profile_changed:
        background_tasks.add_task(_enqueue_match_ranking, current_user.id)
    return response


@router.post("/imports/{resume_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_resume_import(
    resume_id: int,
    current_user: CurrentUser,
    service: ResumeServiceDep,
    db: DbSession,
) -> Response:
    await service.reject_import_draft(user_id=current_user.id, resume_id=resume_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/drafts",
    response_model=TailoredResumeDraftResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_tailored_resume_draft(
    payload: TailoredResumeDraftCreate,
    current_user: CurrentUser,
    service: ResumeServiceDep,
    job_repo: JobRepositoryDep,
) -> TailoredResumeDraftResponse:
    """Create or replace the authenticated user's saved resume for this job."""
    return await service.create_tailored_draft(
        user_id=current_user.id, payload=payload, job_repository=job_repo
    )


@router.post("/coverage", response_model=TailoredResume)
async def preview_resume_coverage(
    payload: TailoredResume,
    current_user: CurrentUser,
    service: ResumeServiceDep,
    job_repo: JobRepositoryDep,
) -> TailoredResume:
    """Preview coverage for the current reviewed snapshot without saving it."""
    return await service.preview_resume_coverage(
        user_id=current_user.id,
        resume=payload,
        job_repository=job_repo,
    )


@router.post("/export-validation", response_model=TailoredResume)
async def validate_resume_export(
    payload: TailoredResume,
    current_user: CurrentUser,
    service: ResumeServiceDep,
    job_repo: JobRepositoryDep,
) -> TailoredResume:
    try:
        return await service.validate_resume_export(
            user_id=current_user.id, resume=payload, job_repository=job_repo
        )
    except TailoredResumeSnapshotInvalidError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/drafts", response_model=list[TailoredResumeDraftListItem])
async def list_tailored_resume_drafts(
    current_user: CurrentUser,
    service: ResumeServiceDep,
) -> list[TailoredResumeDraftListItem]:
    return await service.list_tailored_drafts(user_id=current_user.id)


@router.get("/drafts/{draft_id}", response_model=TailoredResumeDraftResponse)
async def get_tailored_resume_draft(
    draft_id: int,
    current_user: CurrentUser,
    service: ResumeServiceDep,
) -> TailoredResumeDraftResponse:
    return await service.get_tailored_draft(
        user_id=current_user.id, draft_id=draft_id
    )


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tailored_resume_draft(
    draft_id: int,
    current_user: CurrentUser,
    service: ResumeServiceDep,
) -> Response:
    await service.delete_tailored_draft(user_id=current_user.id, draft_id=draft_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/profile", response_model=ProfileResponse)
async def update_profile(
    payload: ProfileUpdate,
    current_user: CurrentUser,
    service: ResumeServiceDep,
    db: DbSession,
    background_tasks: BackgroundTasks,
) -> ProfileResponse:
    response = await service.update_profile(
        user_id=current_user.id, profile=payload.profile
    )
    # Commit before dispatching so the Celery worker always sees the new embedding.
    await db.commit()
    if response.matching_profile_changed:
        background_tasks.add_task(_enqueue_match_ranking, current_user.id)
    return response


@router.get("/tailor/{job_id}", response_model=TailoredResume)
async def tailor_resume(
    job_id: int,
    current_user: CurrentUser,
    service: ResumeServiceDep,
    job_repo: JobRepositoryDep,
) -> TailoredResume:
    """Tailor the user's work history for a specific job posting.

    Rewrites highlights to emphasize JD-relevant experience. Never invents
    skills — only rephrases what the user's profile already contains.
    """
    try:
        return await service.tailor_for_job(
            user_id=current_user.id,
            job_id=job_id,
            job_repository=job_repo,
        )
    except ResumeSourceLayoutUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

