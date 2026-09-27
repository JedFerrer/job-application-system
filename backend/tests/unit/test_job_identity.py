"""Scoped identity and conservative URL regression coverage."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from app.domain.jobs.identity import (
    canonicalize_job_url,
    resolve_source_identity,
    safe_employer_navigation_url,
    scoped_key,
    strong_employer_apply_url,
)

pytestmark = pytest.mark.unit

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0037_scoped_job_identity.py"
)


def _load_migration_0037() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0037_identity", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tracking_variant_keeps_one_validated_native_identity() -> None:
    first = resolve_source_identity("https://boards.greenhouse.io/acme/jobs/123?gh_src=x")
    second = resolve_source_identity("https://boards.greenhouse.io/acme/jobs/123?utm_source=y")
    assert first is not None and second is not None
    assert first.stable_token == second.stable_token
    assert scoped_key("public", first.stable_token) == scoped_key("public", second.stable_token)


def test_same_numeric_greenhouse_id_at_different_tenants_is_not_one_identity() -> None:
    acme = resolve_source_identity("https://boards.greenhouse.io/acme/jobs/123")
    other = resolve_source_identity("https://boards.greenhouse.io/other/jobs/123")
    assert acme is not None and other is not None
    assert acme.stable_token != other.stable_token


def test_unrecognised_semantic_query_parameter_is_preserved() -> None:
    assert canonicalize_job_url("https://example.test/job?a=1&candidate=2#section") == (
        "https://example.test/job?a=1&candidate=2"
    )


def test_url_only_identity_uses_conservative_canonicalization() -> None:
    identity = resolve_source_identity(
        "https://Careers.Example.TEST/jobs/role?utm_campaign=x#apply"
    )
    assert identity is not None
    assert identity.source_job_id is None
    assert identity.stable_token == "url:https://careers.example.test/jobs/role"


def test_strong_employer_apply_url_accepts_recognized_ats_postings() -> None:
    greenhouse = "https://boards.greenhouse.io/acme/jobs/123?gh_src=src"
    lever = "https://jobs.lever.co/acme/abc123de-f456-7890-abcd-ef1234567890"
    workday = "https://acme.myworkdayjobs.com/en-US/job/City/Role_JR12345"
    ashby = "https://jobs.ashbyhq.com/acme/aaaaaaaaaaaaaaaaaaaa"
    assert strong_employer_apply_url(greenhouse) == "https://boards.greenhouse.io/acme/jobs/123"
    assert strong_employer_apply_url(lever) == lever
    assert strong_employer_apply_url(workday) == workday
    assert strong_employer_apply_url(ashby) == ashby


def test_strong_employer_apply_url_rejects_generic_aggregators_and_malformed() -> None:
    assert strong_employer_apply_url("https://careers.example.test/apply/shared") is None
    assert strong_employer_apply_url("https://www.linkedin.com/jobs/view/1234567890/") is None
    assert strong_employer_apply_url("https://www.indeed.com/viewjob?jk=abc123def456") is None
    assert strong_employer_apply_url("https://example.test/jobs/9") is None
    assert strong_employer_apply_url("not-a-url") is None
    assert strong_employer_apply_url("") is None


def test_safe_employer_navigation_url_projects_only_validated_identity_material() -> None:
    lever_id = "abc123de-f456-7890-abcd-ef1234567890"
    cases = (
        (
            "https://boards.greenhouse.io/acme/jobs/123?token=secret&utm_source=x#apply",
            "https://boards.greenhouse.io/acme/jobs/123",
        ),
        (
            f"https://jobs.lever.co/acme/{lever_id}?token=secret#apply",
            f"https://jobs.lever.co/acme/{lever_id}",
        ),
        (
            "https://acme.myworkdayjobs.com/en-US/job/City/Role_JR12345?token=secret#apply",
            "https://acme.myworkdayjobs.com/en-US/job/City/Role_JR12345",
        ),
        (
            "https://jobs.ashbyhq.com/acme/aaaaaaaaaaaaaaaaaaaa?token=secret#apply",
            "https://jobs.ashbyhq.com/acme/aaaaaaaaaaaaaaaaaaaa",
        ),
        (
            "https://www.indeed.com/viewjob?jk=abc123def456&token=secret&utm_source=x#apply",
            "https://www.indeed.com/viewjob?jk=abc123def456",
        ),
        (
            "https://www.linkedin.com/jobs/view/1234567890?token=secret#apply",
            "https://www.linkedin.com/jobs/view/1234567890",
        ),
        (
            "https://www.linkedin.com/jobs/search/?currentJobId=1234567890&token=secret#apply",
            "https://www.linkedin.com/jobs/search/?currentJobId=1234567890",
        ),
    )
    for value, expected in cases:
        assert safe_employer_navigation_url(value) == expected

    assert (
        safe_employer_navigation_url("https://careers.example.test/apply/shared?token=secret")
        is None
    )


def test_malformed_port_is_rejected_without_raising() -> None:
    malformed = "https://example.test:notaport/job"
    assert canonicalize_job_url(malformed) is None
    assert resolve_source_identity(malformed) is None
    assert strong_employer_apply_url(malformed) is None
    assert canonicalize_job_url("https://example.test:70000/job") is None


def test_lookalike_hosts_do_not_receive_trusted_native_identity() -> None:
    evil_linkedin = resolve_source_identity("https://evillinkedin.com/jobs/view/1234567890/")
    not_linkedin = resolve_source_identity("https://notlinkedin.com/jobs/view/1234567890/")
    evil_indeed = resolve_source_identity("https://evilindeed.com/viewjob?jk=abc123def456")
    assert evil_linkedin is not None and evil_linkedin.source_job_id is None
    assert evil_linkedin.source != "linkedin"
    assert not_linkedin is not None and not_linkedin.source_job_id is None
    assert evil_indeed is not None and evil_indeed.source_job_id is None
    assert evil_indeed.source != "indeed"
    assert strong_employer_apply_url("https://evillinkedin.com/jobs/view/1234567890/") is None
    assert strong_employer_apply_url("https://evilindeed.com/viewjob?jk=abc123def456") is None


def test_supported_linkedin_and_indeed_hosts_keep_native_identity() -> None:
    www_linkedin = resolve_source_identity("https://www.linkedin.com/jobs/view/1234567890/")
    apex_linkedin = resolve_source_identity("https://linkedin.com/jobs/view/1234567890/")
    indeed = resolve_source_identity("https://www.indeed.com/viewjob?jk=abc123def456")
    assert www_linkedin is not None and www_linkedin.source == "linkedin"
    assert www_linkedin.source_job_id == "1234567890"
    assert apex_linkedin is not None and apex_linkedin.source == "linkedin"
    assert indeed is not None and indeed.source == "indeed"
    assert indeed.source_job_id == "abc123def456"


def test_workday_posting_route_is_strong_apply_evidence() -> None:
    workday = "https://acme.myworkdayjobs.com/en-US/job/City/Role_JR12345"
    identity = resolve_source_identity(workday)
    assert identity is not None
    assert identity.source == "workday"
    assert identity.source_job_id == "Role_JR12345"
    assert identity.source_tenant == "acme.myworkdayjobs.com"
    assert strong_employer_apply_url(workday) == workday
    assert (
        strong_employer_apply_url("https://acme.myworkdayjobs.com/en-US/job/City/Role_JR12345/")
        == "https://acme.myworkdayjobs.com/en-US/job/City/Role_JR12345/"
    )
    jr_only = "https://acme.myworkdayjobs.com/en-US/job/City/JR12345"
    jr_identity = resolve_source_identity(jr_only)
    assert jr_identity is not None
    assert jr_identity.source == "workday"
    assert jr_identity.source_job_id == "JR12345"
    assert strong_employer_apply_url(jr_only) == jr_only
    requisition = "https://acme.myworkdayjobs.com/en-US/requisition/City/Role_JR12345"
    requisition_identity = resolve_source_identity(requisition)
    assert requisition_identity is not None
    assert requisition_identity.source == "workday"
    assert requisition_identity.source_job_id == "Role_JR12345"
    assert strong_employer_apply_url(requisition) == requisition


def test_workday_non_posting_paths_are_not_strong_apply_evidence() -> None:
    rejected = (
        "https://acme.myworkdayjobs.com/help/job/City/shared1",
        "https://acme.myworkdayjobs.com/help/job/docs/overview",
        "https://acme.myworkdayjobs.com/Careers/job/Remote/Engineer",
        "https://acme.myworkdayjobs.com/xx/job/City/Role_JR12345",
        "https://acme.myworkdayjobs.com/en-US/job/City/shared",
        "https://acme.myworkdayjobs.com/en-US/job/City/shared1",
        "https://acme.myworkdayjobs.com/en-US/job/City/engineer-2024",
        "https://acme.myworkdayjobs.com/en-US/job/Role_JR12345",
        "https://acme.myworkdayjobs.com/en-US/job/City/Role_JR12345/extra",
        "https://acme.myworkdayjobs.com/site/en-US/job/City/Role_JR12345",
        "https://acme.myworkdayjobs.com/job/City/Role_JR12345",
        "https://acme.myworkdayjobs.com/careers",
        "https://acme.myworkdayjobs.com/help",
        "https://evilmyworkdayjobs.com/en-US/job/City/Role_JR12345",
    )
    for url in rejected:
        identity = resolve_source_identity(url)
        assert identity is not None
        assert identity.source != "workday" or identity.source_job_id is None
        assert identity.source_job_id is None
        assert strong_employer_apply_url(url) is None


def test_lever_requires_canonical_uuid_native_id() -> None:
    valid = "https://jobs.lever.co/acme/abc123de-f456-7890-abcd-ef1234567890"
    identity = resolve_source_identity(valid)
    assert identity is not None
    assert identity.source == "lever"
    assert identity.source_job_id == "abc123de-f456-7890-abcd-ef1234567890"
    assert strong_employer_apply_url(valid) == valid
    rejected = (
        "https://jobs.lever.co/acme/------------------------------------",
        "https://jobs.lever.co/acme/abc123def-456-7890-abcd-ef1234567890",
        "https://jobs.lever.co/acme/abc123de-f456-7890-abcd-ef123456789",
        "https://jobs.lever.co/acme/not-a-uuid",
    )
    for url in rejected:
        malformed = resolve_source_identity(url)
        assert malformed is not None
        assert malformed.source_job_id is None
        assert strong_employer_apply_url(url) is None


def test_ashby_requires_hexadecimal_content_in_native_id() -> None:
    valid = "https://jobs.ashbyhq.com/acme/aaaaaaaaaaaaaaaaaaaa"
    identity = resolve_source_identity(valid)
    assert identity is not None
    assert identity.source == "ashby"
    assert identity.source_job_id == "aaaaaaaaaaaaaaaaaaaa"
    assert strong_employer_apply_url(valid) == valid
    rejected = (
        "https://jobs.ashbyhq.com/acme/--------------------",
        "https://jobs.ashbyhq.com/acme/aaaaaaaaaaaaaaaaaaa!",
        "https://jobs.ashbyhq.com/acme/aaaaaaaaaaaaaaaaaaa",
    )
    for url in rejected:
        malformed = resolve_source_identity(url)
        assert malformed is not None
        assert malformed.source_job_id is None
        assert strong_employer_apply_url(url) is None


def test_frozen_migration_identity_matches_runtime_resolver() -> None:
    migration = _load_migration_0037()
    samples = (
        "https://boards.greenhouse.io/acme/jobs/123?utm_source=board",
        "https://jobs.lever.co/acme/abc123de-f456-7890-abcd-ef1234567890",
        "https://www.linkedin.com/jobs/view/555/#section",
        "https://Careers.Example.TEST/jobs/role?utm_campaign=x#apply",
        "https://example.test:notaport/job",
        "https://evillinkedin.com/jobs/view/1234567890/",
        "https://evilindeed.com/viewjob?jk=abc123def456",
        "https://acme.myworkdayjobs.com/en-US/job/City/Role_JR12345",
        "https://acme.myworkdayjobs.com/en-US/job/City/JR12345",
        "https://acme.myworkdayjobs.com/en-US/requisition/City/Role_JR12345",
        "https://acme.myworkdayjobs.com/help/job/City/shared1",
        "https://acme.myworkdayjobs.com/help/job/docs/overview",
        "https://acme.myworkdayjobs.com/en-US/job/City/shared1",
        "https://jobs.ashbyhq.com/acme/aaaaaaaaaaaaaaaaaaaa",
        "https://jobs.ashbyhq.com/acme/--------------------",
        "https://jobs.ashbyhq.com/acme/aaaaaaaaaaaaaaaaaaa!",
        "https://jobs.lever.co/acme/------------------------------------",
        "https://jobs.lever.co/acme/abc123def-456-7890-abcd-ef1234567890",
        "not-a-url",
    )
    for url in samples:
        runtime = resolve_source_identity(url, source_hint="indeed")
        frozen = migration._resolve_source_identity(url, source_hint="indeed")
        if runtime is None:
            assert frozen is None
            continue
        canonical, source, native, tenant = frozen
        assert canonical == runtime.canonical_url
        assert source == runtime.source
        assert native == runtime.source_job_id
        assert tenant == runtime.source_tenant
        assert migration._stable_token(canonical, source, native, tenant) == runtime.stable_token
        assert migration._key("public", runtime.stable_token) == scoped_key(
            "public", runtime.stable_token
        )
