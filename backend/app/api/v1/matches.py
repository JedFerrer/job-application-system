"""Match endpoints (Phase 3): rank + list + explain."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, MatchServiceDep, TaskStoreDep
from app.domain.matching.ranking_dispatch import RankingDispatcher
from app.domain.matching.schemas import (
    MatchEvidence,
    MatchExplanation,
    MatchListResponse,
    MatchSkillAnalysis,
    MatchSummary,
    RankTaskResponse,
    SkillsGapSummary,
)

router = APIRouter(prefix="/matches", tags=["matches"])


@router.post("/rank", response_model=RankTaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def rank_matches(
    current_user: CurrentUser,
    task_store: TaskStoreDep,
) -> RankTaskResponse:
    """Queue the fixed 200-job automatic match refresh for the current user."""
    from app.infrastructure.redis_client import get_redis

    result = await RankingDispatcher(get_redis(), task_store).request_manual_refresh(
        current_user.id
    )
    return RankTaskResponse(**result.__dict__)


@router.get("/tasks/{task_id}")
async def get_ranking_task(
    task_id: str,
    current_user: CurrentUser,
    task_store: TaskStoreDep,
) -> dict[str, object]:
    task = await task_store.get(task_id)
    if task is None or task.get("user_id") != current_user.id:
        raise HTTPException(status_code=404, detail="Task not found or expired")
    return task


@router.post("/{job_id}/analyze", response_model=MatchSummary)
async def analyze_match(
    job_id: int,
    current_user: CurrentUser,
    service: MatchServiceDep,
) -> MatchSummary:
    """Persist an embedding-based match for one visible job without an LLM call."""
    return await service.analyze_job(user_id=current_user.id, job_id=job_id)


@router.get("", response_model=MatchListResponse)
async def list_matches(
    current_user: CurrentUser,
    service: MatchServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int | None, Query(ge=0, deprecated=True)] = None,
    cursor: str | None = Query(default=None, description="Cursor for next page"),
    q: str | None = Query(
        default=None, description="Free-text search across title, company, description"
    ),
    source: str | None = None,
    is_remote: bool | None = None,
    job_type: str | None = None,
    location: str | None = Query(default=None, description="Location filter (ILIKE)"),
    company: str | None = Query(default=None, description="Company name filter (ILIKE)"),
    shift_type: str | None = Query(default=None, description="day | night | mid | flexible"),
    source_type: str | None = Query(default=None, description="global | manual | extension"),
) -> MatchListResponse:
    try:
        return await service.list_matches(
            user_id=current_user.id,
            limit=limit,
            offset=offset,
            cursor=cursor,
            q=q,
            source=source,
            is_remote=is_remote,
            job_type=job_type,
            location_contains=location,
            company_contains=company,
            shift_type=shift_type,
            source_type=source_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc


@router.get("/skills-gap-summary", response_model=SkillsGapSummary)
async def skills_gap_summary(
    current_user: CurrentUser,
    service: MatchServiceDep,
    top_n: Annotated[int, Query(ge=1, le=20)] = 5,
) -> SkillsGapSummary:
    """Top recurring skill gaps across the user's matched jobs."""
    return await service.skills_gap_summary(user_id=current_user.id, top_n=top_n)


@router.get("/{job_id}/summary", response_model=MatchSummary | None)
async def get_match_summary(
    job_id: int,
    current_user: CurrentUser,
    service: MatchServiceDep,
) -> MatchSummary | None:
    """Read persisted match data for job detail without triggering analysis."""
    return await service.get_match_summary(user_id=current_user.id, job_id=job_id)


@router.get("/{job_id}/skills", response_model=MatchSkillAnalysis)
async def analyze_match_skills(
    job_id: int,
    current_user: CurrentUser,
    service: MatchServiceDep,
) -> MatchSkillAnalysis:
    return await service.analyze_skills(user_id=current_user.id, job_id=job_id)


@router.get("/{job_id}/evidence", response_model=MatchEvidence)
async def get_match_evidence(
    job_id: int,
    current_user: CurrentUser,
    service: MatchServiceDep,
) -> MatchEvidence:
    """Build fresh source-based evidence without reading cached match analysis."""
    return await service.get_match_evidence(user_id=current_user.id, job_id=job_id)


@router.get("/{job_id}/explain", response_model=MatchExplanation)
async def explain_match(
    job_id: int,
    current_user: CurrentUser,
    service: MatchServiceDep,
) -> MatchExplanation:
    return await service.explain_match(user_id=current_user.id, job_id=job_id)
