"""Application API schemas (Phase 4 minimal CRM)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ApplicationStatus = Literal[
    "saved", "applied", "interview", "offer", "rejected", "closed"
]


class ApplicationCreate(BaseModel):
    job_id: int


class ApplicationUpdate(BaseModel):
    status: ApplicationStatus | None = None
    cover_letter_id: int | None = None
    notes: str | None = Field(default=None, max_length=10_000)


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    status: str
    cover_letter_id: int | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ApplicationListItem(BaseModel):
    id: int
    job_id: int
    job_title: str
    company: str | None
    location: str | None
    source_url: str
    status: str
    cover_letter_id: int | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ApplicationListResponse(BaseModel):
    items: list[ApplicationListItem]
    total: int
    limit: int = 50
    next_cursor: str | None = None
    """Cursor for the next page (base64-encoded updated_at|id), or None if last page."""
