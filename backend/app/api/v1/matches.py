"""Match endpoints (Phase 3): rank + list + explain."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, MatchServiceDep
from app.domain.matching.schemas import (
    MatchExplanation,
    MatchListResponse,
    RankRequest,
    RankResponse,
    SkillsGapSummary,
)

router = APIRouter(prefix="/matches", tags=["matches"])


@router.post("/rank", response_model=RankResponse)
async def rank_matches(
    payload: RankRequest,
    current_user: CurrentUser,
    service: MatchServiceDep,
) -> RankResponse:
    return await service.rank_jobs(current_user.id, payload.limit)


@router.get("", response_model=MatchListResponse)
async def list_matches(
    current_user: CurrentUser,
    service: MatchServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MatchListResponse:
    return await service.list_matches(
        user_id=current_user.id, limit=limit, offset=offset
    )


@router.get("/skills-gap-summary", response_model=SkillsGapSummary)
async def skills_gap_summary(
    current_user: CurrentUser,
    service: MatchServiceDep,
    top_n: Annotated[int, Query(ge=1, le=20)] = 5,
) -> SkillsGapSummary:
    """Top recurring skill gaps across the user's matched jobs."""
    return await service.skills_gap_summary(user_id=current_user.id, top_n=top_n)


@router.get("/{job_id}/explain", response_model=MatchExplanation)
async def explain_match(
    job_id: int,
    current_user: CurrentUser,
    service: MatchServiceDep,
) -> MatchExplanation:
    return await service.explain_match(user_id=current_user.id, job_id=job_id)
