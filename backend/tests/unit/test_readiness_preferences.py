"""Focused tests for explicit readiness preference rules."""

from types import SimpleNamespace

import pytest

from app.domain.matching.readiness import build_match_readiness

pytestmark = pytest.mark.unit


def _readiness(**kwargs: object):
    job = SimpleNamespace(
        salary_currency="USD",
        salary_max=70_000,
        raw_response={"salary_period": "annual"},
        is_remote=True,
        job_type="fulltime",
        location="New York",
        title="Backend Engineer",
        description="Build Python APIs",
    )
    prefs = SimpleNamespace(
        minimum_annual_salary=None,
        salary_currency=None,
        remote_only=None,
        accepted_job_types=[],
        preferred_locations=[],
        blocked_keywords=[],
    )
    for key, value in kwargs.items():
        if hasattr(job, key):
            setattr(job, key, value)
        else:
            setattr(prefs, key, value)
    return build_match_readiness(
        score=90,
        confidence="strong",
        matched_skills=["python"],
        missing_keywords=[],
        job=job,
        preferences=prefs,
    )


def test_confirmed_remote_and_job_type_blockers_override_apply() -> None:
    readiness = _readiness(
        remote_only=True,
        accepted_job_types=["contract"],
        is_remote=False,
        description="Work in the office",
    )

    assert readiness.decision == "skip"
    assert len(readiness.deterministic_blockers) == 2
    assert readiness.decision_basis == "blocker"


def test_incompatible_salary_currency_is_a_caveat() -> None:
    readiness = _readiness(
        remote_only=True,
        minimum_annual_salary=100_000,
        salary_currency="EUR",
    )

    assert readiness.decision == "apply"
    assert readiness.deterministic_blockers == []
    assert any("salary" in warning.lower() for warning in readiness.deterministic_warnings)


def test_raw_salary_period_does_not_create_a_salary_blocker() -> None:
    readiness = _readiness(
        minimum_annual_salary=100_000,
        salary_currency="USD",
    )

    assert readiness.decision == "apply"
    assert readiness.deterministic_blockers == []
    assert any("normalized" in warning for warning in readiness.deterministic_warnings)


def test_exact_keyword_blocker_does_not_use_substring_matching() -> None:
    readiness = _readiness(blocked_keywords=["java"])
    assert readiness.deterministic_blockers == []

    readiness = _readiness(blocked_keywords=["Python APIs"])
    assert readiness.decision == "skip"
    assert readiness.deterministic_blockers


def test_location_is_warning_only() -> None:
    readiness = _readiness(preferred_locations=["Singapore"])

    assert readiness.decision == "apply"
    assert readiness.deterministic_blockers == []
    assert readiness.deterministic_warnings


def test_hybrid_or_contradictory_remote_text_only_warns() -> None:
    readiness = _readiness(remote_only=True, is_remote=False, description="Hybrid role")
    assert readiness.decision == "apply"
    assert readiness.deterministic_blockers == []
    assert readiness.deterministic_warnings

    readiness = _readiness(
        remote_only=True,
        is_remote=False,
        description="Remote work from home role",
    )
    assert readiness.decision == "apply"
    assert readiness.deterministic_blockers == []

    readiness = _readiness(remote_only=True, is_remote=True, description="Onsite-only role")
    assert readiness.decision == "apply"
    assert readiness.deterministic_blockers == []


def test_clean_confirmed_non_remote_still_blocks() -> None:
    readiness = _readiness(
        remote_only=True,
        is_remote=False,
        description="Work in the office five days per week",
    )
    assert readiness.decision == "skip"
    assert readiness.deterministic_blockers
