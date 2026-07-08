"""Source-adapter contract shared by every job source.

Every concrete source normalizes its payload into a ``RawJobPost`` so the
ingestion pipeline (dedup -> upsert -> embed) is source-agnostic. Adding or
removing a source never touches the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class RawJobPost:
    """Normalized job posting emitted by a source adapter."""

    source: str
    source_url: str
    title: str
    company: str | None = None
    location: str | None = None
    is_remote: bool | None = None
    job_type: str | None = None
    description: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    posted_at: datetime | None = None
    company_logo: str | None = None
    raw: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class JobQuery:
    """Search parameters passed to every source adapter."""

    search_term: str
    location: str | None = None
    country: str = "USA"
    is_remote: bool | None = None
    hours_old: int | None = None
    results_wanted: int = 50
    google_search_term: str | None = None


@runtime_checkable
class JobSource(Protocol):
    """The contract every concrete source adapter implements."""

    name: str

    def fetch(self, query: JobQuery) -> list[RawJobPost]:
        """Return normalized postings for ``query`` (may be empty)."""
        ...
