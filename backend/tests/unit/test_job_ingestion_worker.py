"""Unit tests for scheduled scraping request-building helpers."""

from __future__ import annotations

import pytest

from app.models.user_job_preference import UserJobPreference
from app.workers.job_ingestion import _build_requests, _is_remote_for


def _preference(**overrides: object) -> UserJobPreference:
    pref = UserJobPreference(
        job_titles=["Backend Engineer", "Platform Engineer"],
        keywords=[],
        excluded_keywords=[],
        locations=["Remote", "New York"],
        job_types=[],
        enabled_sources=["indeed", "linkedin"],
        remote_preference="any",
        salary_min=None,
        results_per_source=25,
        is_active=True,
    )
    for key, value in overrides.items():
        setattr(pref, key, value)
    return pref


@pytest.mark.parametrize(
    ("value", "expected"),
    [("remote", True), ("onsite", False), ("hybrid", None), ("any", None), (None, None)],
)
def test_is_remote_for(value: str | None, expected: bool | None) -> None:
    assert _is_remote_for(value) is expected


def test_build_requests_fans_out_titles_and_locations() -> None:
    pref = _preference()

    requests = _build_requests(pref, default_results=50)

    # 2 titles x 2 locations = 4 requests.
    assert len(requests) == 4
    assert {r.search_term for r in requests} == {
        "Backend Engineer",
        "Platform Engineer",
    }
    assert {r.location for r in requests} == {"Remote", "New York"}
    assert all(r.results_wanted == 25 for r in requests)
    assert all(r.sources == ["indeed", "linkedin"] for r in requests)


def test_build_requests_defaults_location_to_none() -> None:
    pref = _preference(locations=[])

    requests = _build_requests(pref, default_results=50)

    assert len(requests) == 2  # 2 titles x [None]
    assert all(r.location is None for r in requests)


def test_build_requests_falls_back_to_default_results() -> None:
    pref = _preference(results_per_source=0)

    requests = _build_requests(pref, default_results=75)

    assert all(r.results_wanted == 75 for r in requests)


def test_build_requests_skips_blank_titles() -> None:
    pref = _preference(job_titles=["Backend Engineer", "  ", ""])

    requests = _build_requests(pref, default_results=50)

    assert {r.search_term for r in requests} == {"Backend Engineer"}


def test_build_requests_sets_remote_flag() -> None:
    pref = _preference(remote_preference="remote", locations=["Anywhere"])

    requests = _build_requests(pref, default_results=50)

    assert all(r.is_remote is True for r in requests)
