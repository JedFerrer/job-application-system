"""Cover-letter endpoints (Phase 4)."""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.api.deps import CoverLetterServiceDep, CurrentUser
from app.domain.cover_letters.schemas import (
    CoverLetterCreate,
    CoverLetterResponse,
    CoverLetterUpdate,
)

router = APIRouter(prefix="/cover-letters", tags=["cover-letters"])


@router.get("/by-job/{job_id}", response_model=CoverLetterResponse)
async def get_cover_letter_by_job(
    job_id: int,
    current_user: CurrentUser,
    service: CoverLetterServiceDep,
) -> CoverLetterResponse | JSONResponse:
    """Return the most recent cover letter for a job, or 404 if none exists."""
    result = await service.get_for_job(user_id=current_user.id, job_id=job_id)
    if result is None:
        return JSONResponse(status_code=404, content={"detail": "No cover letter for this job"})
    return result


@router.post(
    "", response_model=CoverLetterResponse, status_code=status.HTTP_201_CREATED
)
async def create_cover_letter(
    payload: CoverLetterCreate,
    current_user: CurrentUser,
    service: CoverLetterServiceDep,
) -> CoverLetterResponse:
    return await service.generate(
        user_id=current_user.id, job_id=payload.job_id, tone=payload.tone
    )


@router.get("/{cover_letter_id}", response_model=CoverLetterResponse)
async def get_cover_letter(
    cover_letter_id: int,
    current_user: CurrentUser,
    service: CoverLetterServiceDep,
) -> CoverLetterResponse:
    return await service.get(
        user_id=current_user.id, cover_letter_id=cover_letter_id
    )


@router.patch("/{cover_letter_id}", response_model=CoverLetterResponse)
async def update_cover_letter(
    cover_letter_id: int,
    payload: CoverLetterUpdate,
    current_user: CurrentUser,
    service: CoverLetterServiceDep,
) -> CoverLetterResponse:
    return await service.update(
        user_id=current_user.id,
        cover_letter_id=cover_letter_id,
        content=payload.content,
    )
