"""Job API schemas (Phase 2)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

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
    sources: list[JobSourceName] | None = None
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
    # Match data (populated for /imported endpoint)
    match_score: float | None = None
    match_reason: dict[str, object] | None = None


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
