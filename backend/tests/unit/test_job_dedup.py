"""Unit tests for job fingerprinting / dedup."""

from __future__ import annotations

from app.domain.jobs.dedup import job_fingerprint


def test_fingerprint_is_stable_and_16_chars() -> None:
    fp = job_fingerprint("Acme", "Senior Engineer", "Remote")
    assert len(fp) == 16
    assert fp == job_fingerprint("Acme", "Senior Engineer", "Remote")


def test_fingerprint_is_case_and_whitespace_insensitive() -> None:
    a = job_fingerprint("Acme Corp", "Backend Engineer", "Manila")
    b = job_fingerprint("  acme corp ", " backend ENGINEER  ", " MANILA ")
    assert a == b


def test_fingerprint_distinguishes_different_roles() -> None:
    a = job_fingerprint("Acme", "Backend Engineer", "Manila")
    b = job_fingerprint("Acme", "Frontend Engineer", "Manila")
    assert a != b


def test_fingerprint_handles_missing_company_and_location() -> None:
    fp = job_fingerprint(None, "Engineer", None)
    assert len(fp) == 16
    assert fp == job_fingerprint("", "engineer", "")
