"""Unit tests for scheduled scraping request-building helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.jobs.schemas import IngestRequest, SourceRunResult
from app.models.scrape_query import ScrapeQuery
from app.workers.job_ingestion import _request_from_query, _scrape_single_query_async

pytestmark = pytest.mark.unit


def _query(**overrides: object) -> ScrapeQuery:
    """Build a ScrapeQuery with sensible defaults for testing."""
    defaults: dict[str, object] = {
        "id": "q-001",
        "search_term": "Backend Engineer",
        "location": "Remote",
        "is_remote": None,
        "hours_old": 24,
        "results_wanted": 50,
        "sources": ["indeed", "linkedin"],
        "normalized_hash": "abc123def456",
        "status": "active",
        "priority": 5,
        "active_user_count": 2,
    }
    defaults.update(overrides)
    return ScrapeQuery(**defaults)


def test_request_from_query_maps_fields_directly() -> None:
    """_request_from_query maps ScrapeQuery fields 1:1 to IngestRequest."""
    query = _query(
        search_term="Platform Engineer",
        location="New York",
        is_remote=False,
        hours_old=48,
        results_wanted=25,
        sources=["indeed"],
    )

    req = _request_from_query(query)

    assert isinstance(req, IngestRequest)
    assert req.search_term == "Platform Engineer"
    assert req.location == "New York"
    assert req.is_remote is False
    assert req.hours_old == 48
    assert req.results_wanted == 25
    assert req.sources == ["indeed"]


def test_request_from_query_location_can_be_none() -> None:
    """location=None maps through correctly."""
    query = _query(location=None)

    req = _request_from_query(query)

    assert req.location is None


def test_request_from_query_is_remote_can_be_none() -> None:
    """is_remote=None maps through correctly."""
    query = _query(is_remote=None)

    req = _request_from_query(query)

    assert req.is_remote is None


def test_request_from_query_sources_can_be_none() -> None:
    """Empty sources list maps to None via `or None`."""
    query = _query(sources=[])

    req = _request_from_query(query)

    assert req.sources is None


def test_request_from_query_multiple_sources() -> None:
    """All sources are passed through."""
    query = _query(sources=["indeed", "linkedin", "glassdoor"])

    req = _request_from_query(query)

    assert req.sources == ["indeed", "linkedin", "glassdoor"]


@pytest.mark.asyncio
async def test_all_fresh_sources_skip_job_service_and_report_unchanged() -> None:
    """A fresh state is authoritative; it must prevent any provider execution."""
    query = _query()
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: query)

    class SessionContext:
        async def __aenter__(self) -> AsyncMock:
            return db

        async def __aexit__(self, *args: object) -> None:
            return None

    repo = AsyncMock()
    repo.due_sources.return_value = []
    repo.get_source_states.return_value = {
        "indeed": SimpleNamespace(completeness="known_complete"),
        "linkedin": SimpleNamespace(completeness="known_complete"),
    }
    redis = AsyncMock()

    with (
        patch("app.workers.job_ingestion._get_worker_session_factory", return_value=SessionContext),
        patch("app.workers.job_ingestion.ScrapeQueryRepository", return_value=repo),
        patch("app.workers.job_ingestion.aioredis.from_url", return_value=redis),
        patch("app.workers.job_ingestion.JobService") as service,
    ):
        result = await _scrape_single_query_async(query.id)

    assert result["outcome"] == "unchanged"
    assert [item["source"] for item in result["source_results"]] == [
        "indeed", "linkedin"
    ]
    assert all(item["status"] == "skipped_fresh" for item in result["source_results"])
    assert all(
        item["inserted"] == item["updated"] == item["embedded"] == 0
        for item in result["source_results"]
    )
    service.assert_not_called()
    db.commit.assert_awaited_once()
    redis.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_mixed_due_and_fresh_sources_preserve_requested_result_order() -> None:
    """Fresh siblings are reported but never passed to provider execution."""
    query = _query(sources=["indeed", "linkedin", "glassdoor"], next_scrape_at=None)
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: query)

    class SessionContext:
        async def __aenter__(self) -> AsyncMock:
            return db

        async def __aexit__(self, *args: object) -> None:
            return None

    repo = AsyncMock()
    repo.due_sources.return_value = ["indeed", "glassdoor"]
    repo.get_source_states.return_value = {
        "indeed": SimpleNamespace(completeness="known_complete"),
        "linkedin": SimpleNamespace(completeness="known_complete"),
        "glassdoor": SimpleNamespace(completeness="known_complete"),
    }
    subscriptions = AsyncMock()
    subscriptions.get_subscribers.return_value = []
    redis = AsyncMock()
    response = SimpleNamespace(
        inserted=0,
        updated=0,
        subscriber_links=0,
        embedded_job_ids=[],
        source_results=[
            SourceRunResult(
                source="indeed",
                status="success",
                fetched=3,
                accepted=3,
                inserted=2,
                updated=1,
            ),
            SourceRunResult(source="glassdoor", status="empty", completeness="known_complete"),
        ],
    )
    service = AsyncMock()
    service.ingest.return_value = response

    with (
        patch("app.workers.job_ingestion._get_worker_session_factory", return_value=SessionContext),
        patch("app.workers.job_ingestion.ScrapeQueryRepository", return_value=repo),
        patch("app.workers.job_ingestion.SubscriptionRepository", return_value=subscriptions),
        patch("app.workers.job_ingestion.aioredis.from_url", return_value=redis),
        patch("app.workers.job_ingestion.JobService", return_value=service),
    ):
        result = await _scrape_single_query_async(query.id)

    request = service.ingest.await_args.args[0]
    assert request.sources == ["indeed", "glassdoor"]
    assert "linkedin" not in request.sources
    assert [item["source"] for item in result["source_results"]] == [
        "indeed", "linkedin", "glassdoor"
    ]
    fresh = result["source_results"][1]
    assert fresh["status"] == "skipped_fresh"
    assert all(
        fresh[key] == 0
        for key in ("fetched", "accepted", "rejected", "inserted", "updated", "embedded")
    )
    assert [call.kwargs["source"] for call in repo.record_source_result.await_args_list] == [
        "indeed", "glassdoor"
    ]
