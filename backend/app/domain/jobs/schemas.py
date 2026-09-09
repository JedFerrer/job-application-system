"""Job API schemas (Phase 2)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Source identifiers the ingestion endpoint accepts.
JobSourceName = str


class IngestRequest(BaseModel):
    """Body for ``POST /jobs/ingest``."""

    model_config = ConfigDict(str_strip_whitespace=True)

    search_term: str = Field(..., min_length=1, max_length=255)
    location: str | None = Field(default=None, max_length=255)
    country: str | None = Field(default=None, max_length=64)
    is_remote: bool | None = None
    # Default to 3 weeks (504 h) so very stale listings are excluded unless
    # the caller explicitly opts in to a broader window.
    hours_old: int | None = Field(default=504, ge=1, le=720)
    results_wanted: int | None = Field(default=None, ge=1, le=200)
    sources: list[JobSourceName] | None = Field(default=None, min_length=1)
    # Optional override for the Google Jobs search term. When omitted the
    # backend auto-builds one from search_term + location.
    google_search_term: str | None = Field(default=None, max_length=512)


class IngestResponse(BaseModel):
    """Summary returned after a synchronous ingestion run."""

    fetched: int
    inserted: int
    updated: int
    embedded: int
    sources: list[str]
    outcome: str
    source_results: list[SourceRunResult] = Field(default_factory=list)
    # Worker-only handoff; deliberately omitted from public ingestion responses.
    embedded_job_ids: list[int] = Field(default_factory=list, exclude=True)
    # Internal handoff for shared discovery.  This is deliberately excluded
    # from API responses and may contain only committed public job IDs.
    touched_job_ids: list[int] = Field(default_factory=list, exclude=True)
    # Internal count of subscriber relationships committed during shared
    # discovery.  It is not part of the public ingestion contract.
    subscriber_links: int = Field(default=0, exclude=True)


class SourceRunResult(BaseModel):
    source: str
    status: str
    reason_code: str | None = None
    duration_ms: int | None = None
    fetched: int = 0
    # Committed per-source work. These stay zero until that source's
    # persistence/link transaction has succeeded.
    inserted: int = 0
    updated: int = 0
    embedded: int = 0
    accepted: int = 0
    rejected: int = 0
    truncated: bool = False
    truncation_reason: str | None = None
    completeness: str = "unknown"


class SourceCapabilityResponse(BaseModel):
    source: str
    supported_query_fields: list[str]
    country_coverage: list[str] | None = None
    detail_supported: bool
    pagination_mode: str
    availability_evidence_supported: bool
    configuration_available: bool
    passive_health: str
    max_results_per_search: int | None = None


class EmployerSummary(BaseModel):
    """Related active catalog jobs for the detail job's exact employer name."""

    company: str
    company_logo: str | None = None
    job_count: int


class JobSourceLinkResponse(BaseModel):
    """Safe source-alias projection; native IDs and raw payloads stay private."""

    source: str
    source_url: str | None
    canonical_url: str | None
    external_apply_url: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    status: str


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    company: str | None
    location: str | None
    is_remote: bool | None
    job_type: str | None
    description: str | None
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    source: str
    source_url: str
    posted_at: datetime | None
    fetched_at: datetime
    shift_type: str | None
    company_logo: str | None = None
    # Feed-separation fields
    visibility: str
    ingest_source: str
    created_by_user_id: int | None
    imported_at: datetime | None
    status: str
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    expired_at: datetime | None
    not_recently_seen: bool | None = None
    # Match data (populated for /imported endpoint)
    match_score: float | None = None
    match_reason: dict[str, object] | None = None
    employer_summary: EmployerSummary | None = None

    @field_validator("status", mode="before")
    @classmethod
    def _default_missing_legacy_status(cls, value: object) -> str:
        """Keep pre-persistence ORM test doubles compatible with the active default."""
        return "active" if value is None else str(value)


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int
    limit: int
    next_cursor: str | None = None
    """Cursor for the next page (base64-encoded sort_key|id), or None if last page."""


class JobImportRequest(BaseModel):
    """Body for ``POST /jobs/import`` — manually add a single job posting."""

    model_config = ConfigDict(str_strip_whitespace=True)

    url: str = Field(..., min_length=1, max_length=2048)
    title: str = Field(..., min_length=1, max_length=500)
    company: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=20000)
