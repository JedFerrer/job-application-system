"""Source registry — turns requested source names into adapter instances.

Currently only the JobSpy-backed sites are implemented. ``onlinejobs_ph`` and
``jsearch`` adapters are planned (see MVP plan Phase 2) and intentionally not
wired yet; unknown names are ignored so callers stay forward-compatible.
"""

from __future__ import annotations

from app.infrastructure.job_sources.base import (
    JobQuery,
    JobSource,
    RawJobPost,
    SourceCapabilities,
    SourceDetailResult,
    SourceSearchResult,
)
from app.infrastructure.job_sources.jobspy_source import (
    _SUPPORTED_SITES,
    JobSpySource,
)
from app.infrastructure.job_sources.qa_browser_fixtures import build_qa_fixture_source


class UnsupportedJobSource:
    """A requested but unavailable source represented as an honest result."""

    def __init__(self, name: str) -> None:
        self.name = name

    @property
    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            source=self.name, supported_query_fields=(), configuration_available=False
        )

    def search(self, query: JobQuery) -> SourceSearchResult:
        return SourceSearchResult.unsupported(self.name)

    def fetch(self, query: JobQuery) -> list[RawJobPost]:
        return []

    def get_detail(self, source_url: str) -> SourceDetailResult:
        return SourceDetailResult(
            source=self.name, status="unsupported", reason_code="unknown_source"
        )


def build_sources(
    requested: list[str], proxies: list[str] | None = None, *, qa_unit_1a_browser: bool = False
) -> list[JobSource]:
    """Build one result-bearing adapter per distinct requested source in order."""
    sources: list[JobSource] = []
    seen: set[str] = set()
    for name in requested:
        if name in seen:
            continue
        seen.add(name)
        fixture = build_qa_fixture_source(name) if qa_unit_1a_browser else None
        if fixture is not None:
            sources.append(fixture)
        elif name in _SUPPORTED_SITES:
            sources.append(JobSpySource(sites=[name], proxies=proxies))
        else:
            sources.append(UnsupportedJobSource(name))
    return sources
