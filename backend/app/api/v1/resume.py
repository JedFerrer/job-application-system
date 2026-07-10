"""Resume endpoints: upload (async 202 + polling) + profile read/edit."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile, status

from app.api.deps import CurrentUser, JobRepositoryDep, ResumeServiceDep, TaskStoreDep
from app.core.logging import get_logger
from app.domain.resume.exceptions import (
    ProfileNotFoundError,
    ResumeParseError,
    UnsupportedResumeFormatError,
)
from app.domain.resume.repository import ResumeRepository
from app.domain.resume.schemas import (
    ProfileResponse,
    ProfileUpdate,
    ResumeResponse,
    TailoredResume,
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
                from app.domain.resume.service import ResumeService  # local to avoid cycle  # noqa: PLC0415

                service = ResumeService(ResumeRepository(db))
                result = await service.upload_and_parse(
                    user_id=user_id,
                    filename=filename,
                    content=content,
                    progress=_progress,
                )
                await db.commit()
            except (UnsupportedResumeFormatError, ResumeParseError):
                await db.rollback()
                raise
            except Exception:
                await db.rollback()
                raise

        await store.update(
            task_id,
            status="done",
            message=f'Parsed "{filename}"',
            result=result.model_dump(mode="json"),
        )
    except UnsupportedResumeFormatError as exc:
        await store.update(task_id, status="failed", error=exc.detail, message=exc.detail)
    except ResumeParseError:
        msg = "Failed to parse resume with AI"
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


@router.put("/profile", response_model=ProfileResponse)
async def update_profile(
    payload: ProfileUpdate,
    current_user: CurrentUser,
    service: ResumeServiceDep,
) -> ProfileResponse:
    return await service.update_profile(
        user_id=current_user.id, profile=payload.profile
    )


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
    return await service.tailor_for_job(
        user_id=current_user.id,
        job_id=job_id,
        job_repository=job_repo,
    )

