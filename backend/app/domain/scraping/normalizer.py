"""Normalise a ``UserJobPreference`` into shared :class:`ScrapeQuery` hashes.

The canonical hash algorithm is identical to the now-removed
``_search_dedup_key`` in ``workers/job_ingestion.py``.  Two preferences
producing the same (term, location, remote, hours, sources) tuple share one
``ScrapeQuery`` row, so the external job board is hit once per cooldown window
regardless of how many users subscribe.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.models.user_job_preference import UserJobPreference


def _is_remote_for(remote_preference: str | None) -> bool | None:
    match remote_preference:
        case "remote":
            return True
        case "onsite":
            return False
        case _:
            return None


def canonical_key(
    *,
    search_term: str,
    location: str | None,
    is_remote: bool | None,
    hours_old: int,
    sources: list[str],
) -> str:
    """Deterministic, order-independent hash for deduplicating scrape queries.

    The same inputs always produce the same 16-char hex digest so two users
    with identical search parameters subscribe to the same ``ScrapeQuery`` row.
    """
    parts = ":".join(
        [
            search_term.lower().strip(),
            (location or "").lower().strip(),
            str(is_remote),
            str(hours_old),
            ",".join(sorted(sources)),
        ]
    )
    return hashlib.sha256(parts.encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class NormalizedQuery:
    """One scrape query derived from a user preference."""

    search_term: str
    location: str | None
    is_remote: bool | None
    hours_old: int
    results_wanted: int
    sources: list[str]
    normalized_hash: str


def normalize_preference(
    preference: UserJobPreference,
    *,
    default_results: int = 50,
    default_sources: list[str] | None = None,
) -> list[NormalizedQuery]:
    """Fan a single preference row into zero or more normalised queries.

    One query is produced per (job_title × location) combination.  Duplicate
    hashes within the same preference (e.g. two titles that normalise to the
    same search term) are collapsed.
    """
    locations: list[str | None] = list(preference.locations) or [None]
    is_remote = _is_remote_for(preference.remote_preference)
    results = preference.results_per_source or default_results
    sources = list(preference.enabled_sources) or default_sources or []
    hours = preference.hours_old

    seen: set[str] = set()
    queries: list[NormalizedQuery] = []

    for title in preference.job_titles:
        clean = title.strip()
        if not clean:
            continue
        for loc in locations:
            h = canonical_key(
                search_term=clean,
                location=loc,
                is_remote=is_remote,
                hours_old=hours,
                sources=sources,
            )
            if h in seen:
                continue
            seen.add(h)
            queries.append(
                NormalizedQuery(
                    search_term=clean,
                    location=loc,
                    is_remote=is_remote,
                    hours_old=hours,
                    results_wanted=results,
                    sources=sources,
                    normalized_hash=h,
                )
            )

    return queries
