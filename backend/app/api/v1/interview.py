"""Interview prep endpoints (Phase 2)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, InterviewServiceDep
from app.domain.interview.schemas import (
    InterviewPrepRequest,
    InterviewPrepResponse,
)

router = APIRouter(prefix="/interview-prep", tags=["interview"])


@router.post("/{job_id}", response_model=InterviewPrepResponse)
async def generate_interview_prep(
    job_id: int,
    payload: InterviewPrepRequest,
    current_user: CurrentUser,
    service: InterviewServiceDep,
    force_refresh: Annotated[bool, Query()] = False,
) -> InterviewPrepResponse:
    """Return cached interview prep or generate it via the LLM.

    Pass ``?force_refresh=true`` to regenerate and overwrite the cache.
    """
    return await service.get_or_generate(
        user_id=current_user.id,
        job_id=job_id,
        request=payload,
        force_refresh=force_refresh,
    )
