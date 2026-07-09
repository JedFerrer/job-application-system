"""Unit tests for scheduled scraping request-building helpers."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from app.domain.jobs.schemas import IngestRequest
from app.models.scrape_query import ScrapeQuery
from app.workers.job_ingestion import _request_from_query


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
    return ScrapeQuery(**defaults)  # type: ignore[arg-type]


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
