"""Safety and determinism coverage for the isolated Unit 1A Browser QA mode."""

from __future__ import annotations

from app.core.config import Settings
from app.domain.jobs.schemas import IngestRequest
from app.domain.jobs.service import JobService
from app.infrastructure.job_sources.base import JobQuery
from app.infrastructure.job_sources.qa_browser_fixtures import (
    QA_MIXED_FAILURE,
    QA_MIXED_SUCCESS,
    QA_UNCERTAIN_EMPTY,
)
from app.infrastructure.job_sources.registry import build_sources


def test_qa_fixture_mode_requires_test_environment_and_isolated_database() -> None:
    assert not Settings(
        environment="development",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/job_app_test",
        qa_unit_1a_browser_enabled=True,
    ).qa_unit_1a_browser_fixtures_enabled
    assert not Settings(
        environment="test",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/job_app",
        qa_unit_1a_browser_enabled=True,
    ).qa_unit_1a_browser_fixtures_enabled
    assert Settings(
        environment="test",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/job_app_test",
        qa_unit_1a_browser_enabled=True,
    ).qa_unit_1a_browser_fixtures_enabled


def test_qa_fixture_sources_are_unavailable_without_the_guard() -> None:
    source = build_sources([QA_MIXED_SUCCESS])[0]

    assert source.search(JobQuery(search_term="Unit 1A QA")).reason_code == "unknown_source"


def test_qa_fixture_sources_have_deterministic_mixed_and_empty_outcomes() -> None:
    sources = build_sources(
        [QA_MIXED_SUCCESS, QA_MIXED_FAILURE, QA_UNCERTAIN_EMPTY], qa_unit_1a_browser=True
    )
    outcomes = [source.search(JobQuery(search_term="Unit 1A QA")) for source in sources]

    assert [outcome.status for outcome in outcomes] == ["success", "failed", "empty"]
    assert outcomes[0].posts[0].source_url.startswith("https://qa.invalid/")
    assert outcomes[1].reason_code == "provider_error"
    assert outcomes[2].completeness == "unknown"


async def test_qa_fixture_contract_accepts_the_real_ingest_request_defaults() -> None:
    """The default ``hours_old`` must not bypass the guarded fixture registry."""
    from unittest.mock import AsyncMock

    service = JobService(AsyncMock())
    result = await service.ingest(
        IngestRequest(
            search_term="Unit 1A QA",
            country="QA",
            results_wanted=1,
            sources=[QA_MIXED_FAILURE, QA_UNCERTAIN_EMPTY],
        ),
        default_sources=[],
        default_country="QA",
        default_results=1,
        proxies=None,
        qa_unit_1a_browser=True,
    )

    assert [item.status for item in result.source_results] == ["failed", "empty"]
    assert [item.reason_code for item in result.source_results] == ["provider_error", None]
    assert result.outcome == "partial_success"
