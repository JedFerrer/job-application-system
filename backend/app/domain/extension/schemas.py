"""Extension API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ── Auth ────────────────────────────────────────────────────────────────────

class ExtensionPairingStartResponse(BaseModel):
    pairing_code: str
    expires_in: int  # seconds


class ExtensionPairingCompleteRequest(BaseModel):
    pairing_code: str = Field(..., min_length=8, max_length=64)


class ExtensionPairingCompleteResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    user: ExtensionUserResponse
    session_id: str


class ExtensionRefreshRequest(BaseModel):
    refresh_token: str


class ExtensionRefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int


class ExtensionLogoutRequest(BaseModel):
    session_id: str


class ExtensionUserResponse(BaseModel):
    id: int
    email: str
    name: str


# ── Job Import ──────────────────────────────────────────────────────────────

class ExtensionJobImportRequest(BaseModel):
    """Normalized job data from the browser extension."""
    title: str = Field(..., min_length=1, max_length=500)
    company: str = Field(..., min_length=1, max_length=500)
    location: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=50000)
    salary_text: str | None = Field(default=None, max_length=255)
    source_url: str = Field(..., min_length=1, max_length=2048)
    source: str = Field(default="generic", max_length=64)
    ats_type: str | None = Field(default=None, max_length=64)
    external_apply_url: str | None = Field(default=None, max_length=2048)
    raw_html_hash: str | None = Field(default=None, max_length=64)


class ExtensionJobImportResponse(BaseModel):
    job_id: int
    application_id: str | None
    status: str
    was_existing_job: bool
    was_existing_application: bool
    match_status: str  # "queued" | "existing" | "not_applicable"


class ExtensionJobLookupResponse(BaseModel):
    job_id: int | None
    exists: bool
    title: str | None = None
    company: str | None = None


# ── Application Events ──────────────────────────────────────────────────────

EventType = Literal[
    "job_detected",
    "job_imported",
    "apply_clicked",
    "ats_redirect_detected",
    "form_detected",
    "autofill_started",
    "autofill_completed",
    "submit_clicked",
    "confirmation_detected",
    "user_marked_applied",
]


class ApplicationEventRequest(BaseModel):
    """Record an application lifecycle event."""
    job_url: str | None = Field(default=None, max_length=2048)
    event_type: EventType
    page_url: str | None = Field(default=None, max_length=2048)
    metadata: dict[str, object] = Field(default_factory=dict)


class ApplicationEventResponse(BaseModel):
    application_id: str  # UUID as string
    job_id: int
    status: str
    event_recorded: bool


class MarkAppliedRequest(BaseModel):
    job_url: str = Field(..., max_length=2048)
    confirmation_source: Literal["confirmation_detected", "user_marked_applied"]
    confirmation_text: str | None = Field(default=None, max_length=1000)


class MarkAppliedResponse(BaseModel):
    application_id: str
    job_id: int
    status: str
    marked_applied: bool


class ApplicationTimelineResponse(BaseModel):
    application_id: str
    events: list[ApplicationEventItem]


class ApplicationEventItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    source_url: str | None
    page_url: str | None
    metadata: dict[str, object]
    created_at: datetime


# ── Autofill ────────────────────────────────────────────────────────────────

class FieldMappingRequest(BaseModel):
    """Request LLM-based field mapping. Contains field metadata only, never user values."""
    fields: list[FieldMetadata]


class FieldMetadata(BaseModel):
    element_id: str = Field(..., max_length=255)
    tag_name: str = Field(default="input", max_length=32)
    input_type: str | None = Field(default=None, max_length=32)
    label: str | None = Field(default=None, max_length=500)
    placeholder: str | None = Field(default=None, max_length=500)
    options: list[str] | None = Field(default=None)


class FieldMappingItem(BaseModel):
    element_id: str
    profile_path: str | None
    confidence: float
    action: Literal["fill", "review", "skip"]
    reason_skipped: str | None = None


class FieldMappingResponse(BaseModel):
    mappings: list[FieldMappingItem]


class FormFillLogRequest(BaseModel):
    fields: list[dict[str, object]]  # DetectedField serialized
    mappings: list[dict[str, object]]  # FieldMapping serialized
    domain: str | None = None
    ats_type: str | None = None


class FormFillLogResponse(BaseModel):
    session_id: str
    recorded: bool


# ── Extension Session Management ────────────────────────────────────────────

class ExtensionSessionItem(BaseModel):
    id: str
    device_name: str | None
    browser: str | None
    created_at: datetime
    last_used_at: datetime | None
    revoked: bool


class ExtensionSessionListResponse(BaseModel):
    sessions: list[ExtensionSessionItem]
