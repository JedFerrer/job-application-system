"""Cover-letter API schemas (Phase 4)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CoverLetterCreate(BaseModel):
    job_id: int
    tone: str | None = Field(default=None, max_length=32)


class CoverLetterUpdate(BaseModel):
    content: str = Field(min_length=1)


class CoverLetterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    content: str
    tone: str | None
    model: str
    is_edited: bool
    created_at: datetime
    updated_at: datetime
