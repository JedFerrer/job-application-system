"""Application API schemas (Phase 4 minimal CRM)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ApplicationStatus = Literal["saved", "applied", "interview", "offer", "rejected", "closed"]
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


class ApplicationStatusCounts(BaseModel):
    saved: int
    applied: int
    interview: int
    offer: int
    rejected: int
    closed: int


class ApplicationDashboardSummary(BaseModel):
    total_applications: int
    counts_by_status: ApplicationStatusCounts
    active_pipeline_count: int
    offer_count: int
    offer_rate_percent: int


class ApplicationActionCenterDatedItem(BaseModel):
    application_id: int
    job_id: int
    job_title: str
    company: str | None
    application_status: str
    next_action_type: str | None
    next_action_note: str | None
    next_action_due_date: date
    updated_at: datetime


class ApplicationActionCenterMissingItem(BaseModel):
    application_id: int
    job_id: int
    job_title: str
    company: str | None
    application_status: str
    updated_at: datetime


class ApplicationActionCenterDatedGroup(BaseModel):
    count: int
    items: list[ApplicationActionCenterDatedItem]


class ApplicationActionCenterMissingGroup(BaseModel):
    count: int
    items: list[ApplicationActionCenterMissingItem]


class ApplicationActionCenterResponse(BaseModel):
    overdue: ApplicationActionCenterDatedGroup
    due_today: ApplicationActionCenterDatedGroup
    upcoming: ApplicationActionCenterDatedGroup
    needs_next_action: ApplicationActionCenterMissingGroup


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


PreparationState = Literal["not_started", "in_progress", "needs_review", "ready"]
CoverLetterMode = Literal["unset", "selected", "not_needed"]


class ManualQuestion(BaseModel):
    id: UUID | None = None
    question: str = Field(max_length=500)
    answer: str = Field(max_length=4_000)
    acknowledged: bool = False


class PreparationPatch(BaseModel):
    expected_version: int = Field(ge=0)
    fit_acknowledged: bool | None = None
    resume_draft_id: int | None = None
    cover_letter_mode: CoverLetterMode | None = None
    cover_letter_id: int | None = None
    manual_questions: list[ManualQuestion] | None = None

    @model_validator(mode="after")
    def selected_letter_requires_id(self) -> PreparationPatch:
        if self.cover_letter_mode == "selected" and self.cover_letter_id is None:
            raise ValueError("cover_letter_id is required when cover_letter_mode is selected")
        if self.cover_letter_mode == "not_needed" and self.cover_letter_id is not None:
            raise ValueError("cover_letter_id must be null when cover_letter_mode is not_needed")
        if self.manual_questions is not None and len(self.manual_questions) > 20:
            raise ValueError("at most 20 manual questions are allowed")
        return self


class PreparationResponse(BaseModel):
    application_id: int
    version: int
    state: PreparationState
    fit_acknowledged: bool
    fit_context_available: bool
    resume_draft_id: int | None
    resume_current: bool
    cover_letter_mode: CoverLetterMode
    cover_letter_id: int | None
    cover_letter_current: bool
    manual_questions: list[ManualQuestion]
    approved_package_id: int | None
    approval_current: bool


class CreatePackageRequest(BaseModel):
    expected_version: int = Field(ge=0)


class ApplicationPackageResponse(BaseModel):
    id: int
    application_id: int
    revision: int
    content: dict[str, object]
    content_sha256: str
    schema_version: str
    created_at: datetime


class WorkspaceJobContext(BaseModel):
    id: int
    title: str
    company: str | None
    location: str | None
    source_url: str | None
    description: str | None
    status: str | None
    live_context_available: bool
    open_job_available: bool


class WorkspaceDocumentMetadata(BaseModel):
    id: int
    label: str | None = None
    updated_at: datetime | None = None
    is_edited: bool | None = None
    current: bool


class ApplicationPackageHistoryItem(BaseModel):
    id: int
    revision: int
    content_sha256: str
    created_at: datetime
    is_approved: bool


class WorkspaceMatchSummary(BaseModel):
    vector_score: float | None
    llm_score: float | None
    confidence: str | None
    matched_skills: list[str]
    missing_keywords: list[str]


class ApplicationWorkspaceResponse(BaseModel):
    application: ApplicationResponse
    job: WorkspaceJobContext
    match_summary: WorkspaceMatchSummary | None
    preparation: PreparationResponse
    selected_resume: WorkspaceDocumentMetadata | None
    selected_cover_letter: WorkspaceDocumentMetadata | None
    approved_package: ApplicationPackageResponse | None
    package_history: list[ApplicationPackageHistoryItem]
    activity: ApplicationActivityResponse


class ApprovePreparationRequest(BaseModel):
    expected_version: int = Field(ge=0)
    package_id: int = Field(gt=0)
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class AttemptCreateRequest(BaseModel):
    expected_version: int = Field(ge=0)
    package_id: int = Field(gt=0)
    package_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class AttemptResponse(BaseModel):
    attempt_id: str
    expires_at: datetime
    external_url: str


class TimelineEventItem(BaseModel):
    id: str
    type: str
    occurred_at: datetime
    source: str
    summary: str
    counts: dict[str, int] | None = None
    progress: str | None = None


class ApplicationTimelinePage(BaseModel):
    application_id: int
    items: list[TimelineEventItem]
    next_cursor: str | None = None
