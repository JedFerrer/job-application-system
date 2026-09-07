"""Regression coverage for the observable direct-ingestion source contract."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.api.v1 import jobs as jobs_api
from app.domain.jobs.schemas import IngestRequest
from app.domain.jobs.service import JobService
from app.infrastructure.job_sources.base import (
    JobQuery,
    RawJobPost,
    SourceCapabilities,
    SourceDetailResult,
    SourceSearchResult,
)
from app.infrastructure.job_sources.registry import build_sources
from app.models.user import User

pytestmark = pytest.mark.unit


def test_empty_explicit_sources_are_rejected() -> None:
    with pytest.raises(ValidationError):
        IngestRequest(search_term="Engineer", sources=[])


def test_registry_keeps_distinct_requested_sources_in_order() -> None:
    sources = build_sources(["unknown-site", "indeed", "unknown-site", "linkedin"])

    assert [source.name for source in sources] == ["unknown-site", "indeed", "linkedin"]
    unknown = sources[0].search(JobQuery(search_term="Engineer"))
    assert unknown.status == "unsupported"
    assert unknown.reason_code == "unknown_source"


def test_current_adapter_advertises_unknown_coverage_and_unsupported_detail() -> None:
    source = build_sources(["indeed"])[0]

    assert source.capabilities.country_coverage is None
    assert source.capabilities.detail_supported is False
    assert source.capabilities.max_results_per_search == 200
    assert source.get_detail("https://example.test/jobs/1").status == "unsupported"


class FakeSource:
    def __init__(self, result: SourceSearchResult) -> None:
        self.name = result.source
        self._result = result

    @property
    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            source=self.name,
            supported_query_fields=("search_term", "country", "hours_old", "results_wanted"),
        )

    def search(self, query: JobQuery) -> SourceSearchResult:
        return self._result

    def fetch(self, query: JobQuery) -> list[RawJobPost]:
        return self._result.posts

    def get_detail(self, source_url: str) -> SourceDetailResult:
        return SourceDetailResult(source=self.name, status="unsupported")


async def test_ingest_reports_partial_and_total_source_failures_honestly() -> None:
    repository = AsyncMock()
    service = JobService(repository)
    empty = FakeSource(SourceSearchResult(source="indeed", status="empty", completeness="unknown"))
    unavailable = FakeSource(
        SourceSearchResult(
            source="unknown-site", status="unsupported", reason_code="unknown_source"
        )
    )

    with patch("app.domain.jobs.service.build_sources", return_value=[empty, unavailable]):
        partial = await service.ingest(
            IngestRequest(search_term="Engineer"),
            default_sources=["indeed", "unknown-site"],
            default_country="USA",
            default_results=10,
            proxies=None,
        )

    assert partial.outcome == "partial_success"
    assert [item.status for item in partial.source_results] == ["empty", "unsupported"]
    assert partial.source_results[0].completeness == "unknown"

    with patch("app.domain.jobs.service.build_sources", return_value=[unavailable]):
        total_failure = await service.ingest(
            IngestRequest(search_term="Engineer"),
            default_sources=["unknown-site"],
            default_country="USA",
            default_results=10,
            proxies=None,
        )

    assert total_failure.outcome == "failed"
    assert total_failure.source_results[0].reason_code == "unknown_source"


async def test_registry_unknown_source_reaches_ingestion_as_unknown_source() -> None:
    result = await JobService(AsyncMock()).ingest(
        IngestRequest(search_term="Engineer", sources=["not-configured"]),
        default_sources=["indeed"],
        default_country="USA",
        default_results=10,
        proxies=None,
    )

    assert result.outcome == "failed"
    assert result.source_results[0].reason_code == "unknown_source"


async def test_ingest_separates_malformed_rows_from_returned_rows() -> None:
    repository = AsyncMock()
    service = JobService(repository)
    malformed = RawJobPost(source="indeed", source_url="", title="")
    source = FakeSource(
        SourceSearchResult(source="indeed", status="success", posts=[malformed], fetched=1)
    )

    with patch("app.domain.jobs.service.build_sources", return_value=[source]):
        result = await service.ingest(
            IngestRequest(search_term="Engineer"),
            default_sources=["indeed"],
            default_country="USA",
            default_results=10,
            proxies=None,
        )

    assert result.outcome == "success"
    assert result.source_results[0].fetched == 1
    assert result.source_results[0].accepted == 0
    assert result.source_results[0].rejected == 1


async def test_ingest_rejects_known_unsupported_country_and_filter_before_fetch() -> None:
    repository = AsyncMock()
    service = JobService(repository)

    class CountryLimitedSource(FakeSource):
        @property
        def capabilities(self) -> SourceCapabilities:
            return SourceCapabilities(
                source=self.name,
                supported_query_fields=("search_term", "country", "results_wanted"),
                country_coverage=("USA",),
            )

    source = CountryLimitedSource(SourceSearchResult(source="limited", status="empty"))
    with patch("app.domain.jobs.service.build_sources", return_value=[source]):
        result = await service.ingest(
            IngestRequest(search_term="Engineer", country="Canada"),
            default_sources=["limited"],
            default_country="USA",
            default_results=10,
            proxies=None,
        )

    assert result.source_results[0].reason_code == "unsupported_country"


async def test_sources_route_returns_capabilities_without_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jobs_api,
        "get_settings",
        lambda: SimpleNamespace(enabled_job_sources=["indeed"]),
    )

    response = await jobs_api.list_source_capabilities(
        current_user=User(id=1, email="test@example.com", name="Test User")
    )

    assert response[0].source == "indeed"
    assert response[0].max_results_per_search == 200
    assert response[0].country_coverage is None


async def test_background_task_never_publishes_raw_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates: list[dict[str, object]] = []

    class FakeStore:
        async def update(self, task_id: str, **fields: object) -> None:
            updates.append(fields)

    class FailingSession:
        async def __aenter__(self) -> object:
            raise RuntimeError("candidate data and api_key=secret")

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(jobs_api, "TaskStore", lambda redis: FakeStore())
    monkeypatch.setattr(jobs_api, "get_redis", lambda: object())
    monkeypatch.setattr(jobs_api, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(jobs_api, "async_session_factory", lambda: FailingSession())

    await jobs_api._run_ingest_bg("task-1", IngestRequest(search_term="Engineer"), 1)

    assert updates == [{
        "status": "failed",
        "error": jobs_api._SAFE_INGEST_FAILURE_MESSAGE,
        "message": jobs_api._SAFE_INGEST_FAILURE_MESSAGE,
    }]
