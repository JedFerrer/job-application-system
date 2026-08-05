"""Application API schemas (Phase 4 minimal CRM)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ApplicationStatus = Literal[
    "saved", "applied", "interview", "offer", "rejected", "closed"
]
ApplicationNextActionType = Literal[
    "apply",
    "follow_up",
    "prepare_interview",
    "submit_assessment",
    "networking",
    "review_offer",
    "other",
]

MANUAL_NEXT_ACTION_EVENT_CREATED = "manual_next_action_created"
MANUAL_NEXT_ACTION_EVENT_UPDATED = "manual_next_action_updated"
MANUAL_NEXT_ACTION_EVENT_COMPLETED = "manual_next_action_completed"
MANUAL_NEXT_ACTION_EVENT_CLEARED = "manual_next_action_cleared"

MANUAL_NEXT_ACTION_EVENT_TYPES = (
    MANUAL_NEXT_ACTION_EVENT_CREATED,
    MANUAL_NEXT_ACTION_EVENT_UPDATED,
    MANUAL_NEXT_ACTION_EVENT_COMPLETED,
    MANUAL_NEXT_ACTION_EVENT_CLEARED,
)

ManualNextActionEventType = Literal[
    "manual_next_action_created",
    "manual_next_action_updated",
    "manual_next_action_completed",
    "manual_next_action_cleared",
]


class ApplicationCreate(BaseModel):
    job_id: int


class ApplicationUpdate(BaseModel):
    status: ApplicationStatus | None = None
    cover_letter_id: int | None = None
    notes: str | None = Field(default=None, max_length=10_000)
    next_action_type: ApplicationNextActionType | None = None
    next_action_note: str | None = Field(default=None, max_length=10_000)
    next_action_due_date: date | None = None
    next_action_completed_at: datetime | None = None


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    status: str
    cover_letter_id: int | None
    notes: str | None
    next_action_type: str | None
    next_action_note: str | None
    next_action_due_date: date | None
    next_action_completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApplicationListItem(BaseModel):
    id: int
    job_id: int
    job_title: str
    company: str | None
    location: str | None
    company_logo: str | None = None
    source_url: str
    status: str
    cover_letter_id: int | None
    notes: str | None
    next_action_type: str | None
    next_action_note: str | None
    next_action_due_date: date | None
    next_action_completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApplicationListResponse(BaseModel):
    items: list[ApplicationListItem]
    total: int
    limit: int = 50
    next_cursor: str | None = None
    """Cursor for the next page (base64-encoded updated_at|id), or None if last page."""


class ManualNextActionActivitySnapshot(BaseModel):
    type: str | None
    due_date: date | None
    completed: bool
    has_note: bool


class ManualNextActionActivityMetadata(BaseModel):
    source: str
    previous: ManualNextActionActivitySnapshot
    current: ManualNextActionActivitySnapshot
    changed_fields: list[str]


class ApplicationActivityEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: ManualNextActionEventType
    metadata: ManualNextActionActivityMetadata
    created_at: datetime


class ApplicationActivityResponse(BaseModel):
    application_id: int
    events: list[ApplicationActivityEvent]
