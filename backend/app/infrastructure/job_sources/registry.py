"""Source registry — turns requested source names into adapter instances.

Currently only the JobSpy-backed sites are implemented. ``onlinejobs_ph`` and
``jsearch`` adapters are planned (see MVP plan Phase 2) and intentionally not
wired yet; unknown names are ignored so callers stay forward-compatible.
"""

from __future__ import annotations

from app.infrastructure.job_sources.base import JobSource
from app.infrastructure.job_sources.jobspy_source import (
    _SUPPORTED_SITES,
    JobSpySource,
)


def build_sources(
    requested: list[str], proxies: list[str] | None = None
) -> list[JobSource]:
    """Build one adapter per requested site so a single failing source
    (e.g. Glassdoor on an unsupported country) cannot abort all others."""
    sources: list[JobSource] = []
    for name in requested:
        if name in _SUPPORTED_SITES:
            sources.append(JobSpySource(sites=[name], proxies=proxies))
    return sources
