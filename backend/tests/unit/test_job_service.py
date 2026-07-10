"""Unit tests for JobService.get_job."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.domain.jobs.exceptions import JobNotFoundError
from app.domain.jobs.schemas import JobResponse
from app.domain.jobs.service import JobService


@pytest.fixture
def mock_repo() -> AsyncMock:
    """Return a mock JobRepository with a get_by_id method."""
    repo = AsyncMock()
    repo.get_by_id = AsyncMock()
    return repo


@pytest.fixture
def service(mock_repo: AsyncMock) -> JobService:
    """Return a JobService with the mock repository."""
    return JobService(mock_repo)


async def test_get_job_returns_job_response(service: JobService, mock_repo: AsyncMock) -> None:
    """get_job calls repo.get_by_id and returns a JobResponse."""
    from app.models.job import Job

    job = Job(
        id=1,
        fingerprint="abc1234567890def",
        title="Senior Engineer",
        company="Acme Corp",
        location="Manila",
        source="linkedin",
        source_url="https://example.com/job/1",
        raw_response={},
        fetched_at=__import__("datetime").datetime(2026, 1, 1),
        visibility="public",
        ingest_source="scraper",
    )
    mock_repo.get_by_id.return_value = job

    result = await service.get_job(1)

    mock_repo.get_by_id.assert_called_once_with(1)
    assert isinstance(result, JobResponse)
    assert result.id == 1
    assert result.title == "Senior Engineer"
    assert result.company == "Acme Corp"
    assert result.source_url == "https://example.com/job/1"


async def test_get_job_raises_not_found(service: JobService, mock_repo: AsyncMock) -> None:
    """get_job raises JobNotFoundError when repo returns None."""
    mock_repo.get_by_id.return_value = None

    with pytest.raises(JobNotFoundError):
        await service.get_job(999)
