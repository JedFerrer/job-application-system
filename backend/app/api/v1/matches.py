"""Match endpoints (Phase 3): rank + list + explain."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser, MatchServiceDep
from app.domain.matching.schemas import (
    MatchEvidence,
    MatchExplanation,
    MatchListResponse,
    MatchSkillAnalysis,
    MatchSummary,
    RankRequest,
    RankResponse,
    SkillsGapSummary,
)

router = APIRouter(prefix="/matches", tags=["matches"])


@router.post("/rank", response_model=RankResponse)
async def rank_matches(
    current_user: CurrentUser,
    service: MatchServiceDep,
    payload: RankRequest = RankRequest(),
) -> RankResponse:
    return await service.rank_jobs(current_user.id, payload.limit)


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
