"""Job endpoints: ingest (async 202 + polling) + list + detail."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

from app.api.deps import CurrentUser, JobServiceDep, TaskStoreDep
from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.jobs.embedding_coordinator import JobEmbeddingCoordinator
from app.domain.jobs.repository import JobRepository
from app.domain.jobs.schemas import (
    IngestRequest,
    IngestResponse,
    JobImportRequest,
    JobListResponse,
    JobResponse,
)
from app.domain.matching.job_skill_mention_repository import JobSkillMentionRepository
from app.domain.matching.job_skill_mentions import JobSkillMentionService
from app.infrastructure.database import async_session_factory
from app.infrastructure.redis_client import get_redis
from app.infrastructure.task_store import TaskStore

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------


async def _run_ingest_bg(task_id: str, request: IngestRequest, user_id: int) -> None:
    """Runs after 202 is returned: scrapes sources, upserts, embeds."""
    store = TaskStore(get_redis())
    settings = get_settings()

    async def _progress(**kw: Any) -> None:
        await store.update(task_id, **kw)

    try:
        async with async_session_factory() as db:
            try:
                from app.domain.jobs.service import (
                    JobService,
                )  # local to avoid module-level cycle  # noqa: PLC0415

                repository = JobRepository(db)
                service = JobService(
                    repository,
                    user_timezone=settings.user_timezone,
                    embedding_coordinator=JobEmbeddingCoordinator(repository, get_redis()),
                    skill_mention_service=JobSkillMentionService(
                        JobSkillMentionRepository(db)
                    ),
                )
                result = await service.ingest(
                    request,
                    default_sources=settings.enabled_job_sources,
                    default_country=settings.job_search_country,
                    default_results=settings.job_results_wanted,
                    proxies=settings.proxy_list or None,
                    progress=_progress,
                    user_id=user_id,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

        await store.update(
            task_id,
            status="done",
            message=(
                f"Done — {result.fetched} fetched, "
                f"{result.inserted} new, {result.embedded} embedded"
            ),
            fetched=result.fetched,
            inserted=result.inserted,
            updated=result.updated,
            embedded=result.embedded,
            sources=result.sources,
            result=result.model_dump(),
        )
    except Exception as exc:
        logger.exception("bg_ingest.failed", task_id=task_id)
        await store.update(
            task_id,
            status="failed",
            error=str(exc),
            message=f"Failed: {exc}",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_jobs(
    payload: IngestRequest,
    current_user: CurrentUser,
    task_store: TaskStoreDep,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Queue a job-scraping run.  Poll ``GET /jobs/tasks/{task_id}`` for progress."""
    task_id = await task_store.create("ingest", user_id=current_user.id)
    background_tasks.add_task(_run_ingest_bg, task_id, payload, current_user.id)
    return {"task_id": task_id}


@router.get("/tasks/{task_id}")
async def get_ingest_task(
    task_id: str,
    current_user: CurrentUser,
    task_store: TaskStoreDep,
) -> dict[str, Any]:
    """Poll ingest task progress.  Expires after 1 hour."""
    task = await task_store.get(task_id)
    if task is None or task.get("user_id") != current_user.id:
        raise HTTPException(status_code=404, detail="Task not found or expired")
    return task


@router.get("", response_model=JobListResponse)
async def list_jobs(
    current_user: CurrentUser,
    service: JobServiceDep,
    source: str | None = None,
    is_remote: bool | None = None,
    job_type: str | None = None,
    location: str | None = None,
    company: str | None = Query(default=None, description="Company name filter (ILIKE)"),
    company_exact: str | None = Query(
        default=None,
        description="Exact company name filter (case- and whitespace-normalized)",
    ),
    q: str | None = Query(
        default=None, description="Free-text search across title, company, description"
    ),
    min_salary: float | None = Query(default=None, ge=0),
    shift_type: str | None = Query(default=None, description="day | night | mid | flexible"),
    source_type: str | None = Query(
        default=None, description="global | imported | manual | extension"
    ),
    imported_only: bool = False,
    archive_state: Literal["active", "archived"] = "active",
    cursor: str | None = Query(default=None, description="Cursor for next page"),
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> JobListResponse:
    return await service.list_jobs(
        user_id=current_user.id,
        q=q,
        source=source,
        is_remote=is_remote,
        min_salary=min_salary,
        job_type=job_type,
        location_contains=location,
        company_contains=company,
        company_exact=company_exact,
        shift_type=shift_type,
        source_type=source_type,
        imported_only=imported_only,
        archive_state=archive_state,
        cursor=cursor,
        limit=limit,
    )


@router.get("/imported", response_model=JobListResponse)
async def list_imported_jobs(
    current_user: CurrentUser,
    service: JobServiceDep,
    cursor: str | None = Query(default=None),
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> JobListResponse:
    """Jobs imported by the current user (manual + extension)."""
    return await service.list_imported_jobs(user_id=current_user.id, cursor=cursor, limit=limit)


@router.post("/import", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def import_job(
    payload: JobImportRequest,
    current_user: CurrentUser,
    service: JobServiceDep,
) -> JobResponse:
    """Manually import a single job posting from a URL."""
    return await service.import_job(payload, user_id=current_user.id)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: int,
    current_user: CurrentUser,
    service: JobServiceDep,
) -> JobResponse:
    return await service.get_job(user_id=current_user.id, job_id=job_id)


@router.patch("/{job_id}/archive", status_code=status.HTTP_204_NO_CONTENT)
async def archive_job(
    job_id: int,
    current_user: CurrentUser,
    service: JobServiceDep,
) -> None:
    """Per-user archive — only affects the current user's view of this job."""
    await service.archive_job(current_user.id, job_id)


@router.patch("/{job_id}/restore", status_code=status.HTTP_204_NO_CONTENT)
async def restore_job(
    job_id: int,
    current_user: CurrentUser,
    service: JobServiceDep,
) -> None:
    """Restore one of the current user's archived jobs to the active feed."""
    await service.restore_job(current_user.id, job_id)
