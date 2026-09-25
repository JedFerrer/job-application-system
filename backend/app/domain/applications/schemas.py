# ruff: noqa: E501
"""Application API schemas (Phase 4 minimal CRM)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.applications.artifacts import ArtifactValidationReport

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


class ArtifactSummary(BaseModel):
    id: int
    package_id: int
    document_type: Literal["resume", "cover_letter"]
    format: Literal["pdf", "docx"]
    filename: str
    byte_length: int
    sha256: str
    validation_state: Literal["queued", "validating", "validated", "warning", "failed", "unavailable"]
    failure_category: str | None
    created_at: datetime


class ArtifactDetail(ArtifactSummary):
    application_id: int
    content_type: str
    exporter_version: str
    expected_text_sha256: str
    validation_policy_version: str
    validation_report: ArtifactValidationReport | None
    task_id: str | None
    retry_after: datetime | None


class ArtifactUploadResponse(BaseModel):
    artifact: ArtifactDetail
    existing: bool


class ApplicationPackageResponse(BaseModel):
    id: int
    application_id: int
    revision: int
    content: dict[str, object]
    content_sha256: str
    schema_version: str
    created_at: datetime
    artifacts: list[ArtifactSummary] = Field(default_factory=list)


ReviewState = Literal["queued", "running", "completed", "failed"]
ReviewOutcome = Literal["no_findings", "attention_needed", "insufficient_evidence"]
ReviewDisposition = Literal["keep_wording", "will_revise"]


class DocumentReviewFinding(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    document: Literal["resume", "cover_letter"]
    category: Literal["unsupported_claim", "cross_document_mismatch", "job_document_mismatch", "omitted_verified_evidence", "repetition", "clarity", "keyword_stuffing"]
    severity: Literal["concern", "suggestion"]
    excerpt: str = Field(min_length=1, max_length=100_000)
    locator: str = Field(min_length=1, max_length=512)
    rationale: str = Field(min_length=1, max_length=2_000)
    evidence: list[DocumentReviewEvidence] = Field(default_factory=list, max_length=50)
    proposed_action: str = Field(min_length=1, max_length=2_000)


class DocumentReviewEvidence(BaseModel):
    role: Literal["target", "comparison", "requirement"]
    document: Literal["job", "resume", "cover_letter"]
    locator: str = Field(min_length=1, max_length=512)
    excerpt: str = Field(min_length=1, max_length=100_000)


class ProviderFindingV6(BaseModel):
    """Provider-owned review semantics with server-derived evidence roles."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    category: Literal["unsupported_claim", "cross_document_mismatch", "job_document_mismatch", "omitted_verified_evidence", "repetition", "clarity", "keyword_stuffing"]
    severity: Literal["concern", "suggestion"]
    rationale: str = Field(min_length=1, max_length=2_000)
    evidence_ids: list[Annotated[str, Field(min_length=1, max_length=32)]] = Field(min_length=1, max_length=2)
    proposed_action: str = Field(min_length=1, max_length=2_000)


class ProviderFindingV7(BaseModel):
    """Provider-owned review semantics for the v7 ID-only contract."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    category: Literal["unsupported_claim", "cross_document_mismatch", "job_document_mismatch", "omitted_verified_evidence", "repetition", "clarity", "keyword_stuffing"]
    severity: Literal["concern", "suggestion"]
    rationale: str = Field(min_length=1, max_length=2_000)
    evidence_ids: list[Annotated[str, Field(min_length=1, max_length=32)]] = Field(min_length=1, max_length=2)
    proposed_action: str = Field(min_length=1, max_length=2_000)


class ProviderDocumentReviewResponseV7(BaseModel):
    """Strict internal boundary for the v7 provider response.

    ``findings`` deliberately has no default: an omitted top-level field is a
    schema failure, while an explicit empty list remains a valid no-findings response.
    """

    model_config = ConfigDict(extra="forbid")
    findings: list[ProviderFindingV7] = Field(..., max_length=20)


class ProviderFindingV8(BaseModel):
    """Provider-owned review semantics for the v8 ID-only contract."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    category: Literal["unsupported_claim", "cross_document_mismatch", "job_document_mismatch", "omitted_verified_evidence", "repetition", "clarity", "keyword_stuffing"]
    severity: Literal["concern", "suggestion"]
    rationale: str = Field(min_length=1, max_length=2_000)
    evidence_ids: list[Annotated[str, Field(min_length=1, max_length=32)]] = Field(min_length=1, max_length=2)
    proposed_action: str = Field(min_length=1, max_length=2_000)


class ProviderDocumentReviewResponseV8(BaseModel):
    """Strict internal boundary for the v8 provider response.

    ``findings`` deliberately has no default: an omitted top-level field is a
    schema failure, while an explicit empty list remains a valid no-findings response.
    """

    model_config = ConfigDict(extra="forbid")
    findings: list[ProviderFindingV8] = Field(..., max_length=20)


class _ProviderFindingV10Base(BaseModel):
    """Common provider-owned fields; evidence semantics remain server-owned."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    severity: Literal["concern", "suggestion"]
    rationale: str = Field(min_length=1, max_length=2_000)
    proposed_action: str = Field(min_length=1, max_length=2_000)


class ProviderSingleTargetFindingV10(_ProviderFindingV10Base):
    category: Literal["unsupported_claim", "repetition", "clarity", "keyword_stuffing"]
    target_evidence_id: Annotated[str, Field(min_length=1, max_length=32, pattern=r"^(?:resume|cover)-e[0-9]{4,}$")]


class ProviderCrossDocumentFindingV10(_ProviderFindingV10Base):
    category: Literal["cross_document_mismatch"]
    resume_evidence_id: Annotated[str, Field(min_length=1, max_length=32, pattern=r"^resume-e[0-9]{4,}$")]
    cover_letter_evidence_id: Annotated[str, Field(min_length=1, max_length=32, pattern=r"^cover-e[0-9]{4,}$")]


class ProviderJobTargetFindingV10(_ProviderFindingV10Base):
    category: Literal["job_document_mismatch", "omitted_verified_evidence"]
    job_evidence_id: Annotated[str, Field(min_length=1, max_length=32, pattern=r"^job-e[0-9]{4,}$")]
    target_evidence_id: Annotated[str, Field(min_length=1, max_length=32, pattern=r"^(?:resume|cover)-e[0-9]{4,}$")]


type ProviderFindingV10 = Annotated[
    ProviderSingleTargetFindingV10 | ProviderCrossDocumentFindingV10 | ProviderJobTargetFindingV10,
    Field(discriminator="category"),
]


class ProviderDocumentReviewResponseV10(BaseModel):
    """Strict V10 boundary: category-specific, document-qualified selections."""

    model_config = ConfigDict(extra="forbid")
    findings: list[ProviderFindingV10] = Field(..., max_length=20)


class ReviewAcknowledgmentItem(BaseModel):
    finding_id: str = Field(min_length=1, max_length=64)
    disposition: ReviewDisposition


class ReviewAcknowledgmentRequest(BaseModel):
    package_content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    acknowledgments: list[ReviewAcknowledgmentItem] = Field(min_length=1, max_length=20)


class DocumentReviewResponse(BaseModel):
    id: str
    application_id: int
    package_id: int
    package_content_sha256: str
    review_policy_version: str
    state: ReviewState
    outcome: ReviewOutcome | None = None
    findings: list[DocumentReviewFinding] = Field(default_factory=list)
    failure_code: str | None = None
    acknowledgments: list[ReviewAcknowledgmentItem] = Field(default_factory=list)
    created_at: datetime
    completed_at: datetime | None = None


class DocumentReviewRequest(BaseModel):
    rerun: bool = False


class DocumentReviewQueuedResponse(BaseModel):
    task_id: str
    review_id: str
    state: Literal["queued", "running"]
    existing: bool = False


class DocumentReviewSummary(BaseModel):
    id: str
    task_id: str | None = None
    package_id: int
    package_content_sha256: str
    review_policy_version: str
    state: ReviewState
    outcome: ReviewOutcome | None = None
    failure_code: str | None = None


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
    artifacts: list[ArtifactSummary] = Field(default_factory=list)


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
    current_reviewable_package: ApplicationPackageResponse | None = None
    package_history: list[ApplicationPackageHistoryItem]
    activity: ApplicationActivityResponse
    current_document_review: DocumentReviewSummary | None = None


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


SubmissionMaterialChoice = Literal[
    "used_selected_package", "used_other_material", "materials_unknown"
]


class SubmissionConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_choice: SubmissionMaterialChoice
    package_id: int | None = None
    artifact_ids: list[int] = Field(default_factory=list)
    attempt_id: str | None = None
    submitted_at: datetime | None = None
    note: str | None = Field(default=None, max_length=10_000)

    @model_validator(mode="after")
    def material_links_match_choice(self) -> SubmissionConfirmRequest:
        selected = self.material_choice == "used_selected_package"
        if selected != (self.package_id is not None):
            raise ValueError("package_id must be present only for selected package material")
        if not selected and self.artifact_ids:
            raise ValueError("artifact_ids require selected package material")
        if len(self.artifact_ids) != len(set(self.artifact_ids)):
            raise ValueError("artifact_ids must be unique")
        if self.submitted_at is not None and self.submitted_at.tzinfo is None:
            raise ValueError("submitted_at must include a time zone")
        return self


class SubmissionResponse(BaseModel):
    id: int
    application_id: int
    attempt_id: str | None
    package_id: int | None
    artifact_ids: list[int]
    artifacts: list[ArtifactSummary] = Field(default_factory=list)
    material_choice: SubmissionMaterialChoice
    provenance: str
    submitted_at: datetime
    submitted_at_is_user_reported: bool
    note: str | None
    created_at: datetime


class SubmissionListResponse(BaseModel):
    items: list[SubmissionResponse]
    next_cursor: str | None = None


class TimelineEventItem(BaseModel):
    id: str
    type: str
    occurred_at: datetime
    source: str
    summary: str
    submission_id: int | None = None
    counts: dict[str, int] | None = None
    progress: str | None = None


class ApplicationTimelinePage(BaseModel):
    application_id: int
    items: list[TimelineEventItem]
    next_cursor: str | None = None
