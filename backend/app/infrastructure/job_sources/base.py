"""Source-adapter contract shared by every job source.

Every concrete source normalizes its payload into a ``RawJobPost`` so the
ingestion pipeline (dedup -> upsert -> embed) is source-agnostic. Adding or
removing a source never touches the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from time import perf_counter
from typing import Literal, Protocol, runtime_checkable

SourceStatus = Literal[
    "queued", "running", "success", "empty", "unsupported", "unavailable", "failed", "skipped_fresh"
]
ReasonCode = Literal[
    "unsupported_country", "unsupported_filter", "unknown_source", "dependency_missing",
    "rate_limited", "timeout", "parse_error", "provider_error", "fresh_cache",
]
Completeness = Literal["known_complete", "unknown"]


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
    # An employer application URL is optional source evidence, never inferred.
    external_apply_url: str | None = None
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


@dataclass(frozen=True, slots=True)
class SourceCapabilities:
    """Static, safe-to-publish behaviour advertised by one source adapter."""

    source: str
    supported_query_fields: tuple[str, ...]
    country_coverage: tuple[str, ...] | None = None
    detail_supported: bool = False
    pagination_mode: Literal["internal", "none"] = "none"
    availability_evidence_supported: bool = False
    configuration_available: bool = True
    passive_health: Literal["available", "unknown"] = "unknown"
    max_results_per_search: int | None = None


@dataclass(slots=True)
class SourceSearchResult:
    """One source's normalized search outcome; provider payloads never escape it."""

    source: str
    status: SourceStatus
    posts: list[RawJobPost] = field(default_factory=list)
    reason_code: ReasonCode | None = None
    duration_ms: int | None = None
    fetched: int = 0
    accepted: int = 0
    rejected: int = 0
    truncated: bool = False
    truncation_reason: str | None = None
    completeness: Completeness = "unknown"

    @classmethod
    def unsupported(
        cls, source: str, reason_code: ReasonCode = "unknown_source"
    ) -> SourceSearchResult:
        return cls(source=source, status="unsupported", reason_code=reason_code)


@dataclass(frozen=True, slots=True)
class SourceDetailResult:
    """Detail lookup contract. Current adapters deliberately return unsupported."""

    source: str
    status: Literal["success", "unsupported", "unavailable", "failed"]
    reason_code: ReasonCode | None = None
    post: RawJobPost | None = None


@runtime_checkable
class JobSource(Protocol):
    """The contract every concrete source adapter implements."""

    name: str

    @property
    def capabilities(self) -> SourceCapabilities: ...

    def search(self, query: JobQuery) -> SourceSearchResult:
        """Return posts and source-level metadata for one query."""
        ...

    def fetch(self, query: JobQuery) -> list[RawJobPost]:
        """Compatibility wrapper returning only normalized postings."""
        ...

    def get_detail(self, source_url: str) -> SourceDetailResult:
        """Return a normalized detail result when the adapter supports it."""
        ...


def fetch_compat_result(source: JobSource, query: JobQuery) -> SourceSearchResult:
    """Adapt legacy fetch-only test/third-party adapters during this transition."""
    started = perf_counter()
    posts = source.fetch(query)
    elapsed = int((perf_counter() - started) * 1000)
    return SourceSearchResult(
        source=source.name,
        status="success" if posts else "empty",
        posts=posts,
        duration_ms=elapsed,
        fetched=len(posts),
        completeness="unknown",
    )
