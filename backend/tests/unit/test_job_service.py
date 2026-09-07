"""Unit tests for JobService.get_job."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.jobs.exceptions import JobNotFoundError
from app.domain.jobs.identity import scoped_key
from app.domain.jobs.schemas import IngestRequest, JobResponse
from app.domain.jobs.service import JobService
from app.infrastructure.job_sources.base import RawJobPost

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_repo() -> AsyncMock:
    """Return a mock JobRepository with a get_visible_by_id method."""
    repo = AsyncMock()
    repo.get_visible_by_id = AsyncMock()
    repo.count_active_public_jobs_by_company = AsyncMock(return_value=0)
    return repo


@pytest.fixture
def service(mock_repo: AsyncMock) -> JobService:
    """Return a JobService with the mock repository."""
    return JobService(mock_repo)


async def test_get_job_returns_job_response(service: JobService, mock_repo: AsyncMock) -> None:
    """get_job calls repo.get_visible_by_id and returns a JobResponse."""
    from app.models.job import Job

    job = Job(
        id=1,
        fingerprint="abc1234567890def",
        identity_scope="public",
        identity_key=scoped_key("public", "legacy:job-service:get-job"),
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
    mock_repo.get_visible_by_id.return_value = job

    result = await service.get_job(user_id=1, job_id=1)

    mock_repo.get_visible_by_id.assert_called_once_with(user_id=1, job_id=1)
    assert isinstance(result, JobResponse)
    assert result.id == 1
    assert result.title == "Senior Engineer"
    assert result.company == "Acme Corp"
    assert result.source_url == "https://example.com/job/1"


async def test_get_job_includes_employer_summary_for_multiple_public_jobs(
    service: JobService, mock_repo: AsyncMock
) -> None:
    from app.models.job import Job

    job = Job(
        id=1,
        fingerprint="abc1234567890def",
        identity_scope="public",
        identity_key=scoped_key("public", "legacy:job-service:employer-summary"),
        title="Senior Engineer",
        company="  Acme   Corp ",
        location="Manila",
        source="linkedin",
        source_url="https://example.com/job/1",
        raw_response={},
        fetched_at=__import__("datetime").datetime(2026, 1, 1),
        visibility="public",
        ingest_source="scraper",
    )
    mock_repo.get_visible_by_id.return_value = job
    mock_repo.count_active_public_jobs_by_company.return_value = 4

    result = await service.get_job(user_id=1, job_id=1)

    mock_repo.count_active_public_jobs_by_company.assert_awaited_once_with(
        user_id=1,
        company="Acme Corp",
    )
    assert result.employer_summary is not None
    assert result.employer_summary.company == "Acme Corp"
    assert result.employer_summary.job_count == 4


async def test_get_job_omits_employer_summary_for_private_jobs(
    service: JobService, mock_repo: AsyncMock
) -> None:
    from app.models.job import Job

    job = Job(
        id=1,
        fingerprint="abc1234567890def",
        identity_scope="user:1",
        identity_key=scoped_key("user:1", "legacy:job-service:private"),
        title="Senior Engineer",
        company="Acme Corp",
        location="Manila",
        source="manual",
        source_url="https://example.com/job/1",
        raw_response={},
        fetched_at=__import__("datetime").datetime(2026, 1, 1),
        visibility="private",
        ingest_source="manual",
    )
    mock_repo.get_visible_by_id.return_value = job

    result = await service.get_job(user_id=1, job_id=1)

    mock_repo.count_active_public_jobs_by_company.assert_not_awaited()
    assert result.employer_summary is None


async def test_get_job_omits_employer_summary_for_a_single_related_job(
    service: JobService, mock_repo: AsyncMock
) -> None:
    from app.models.job import Job

    job = Job(
        id=1,
        fingerprint="abc1234567890def",
        identity_scope="public",
        identity_key=scoped_key("public", "legacy:job-service:single-related"),
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
    mock_repo.get_visible_by_id.return_value = job
    mock_repo.count_active_public_jobs_by_company.return_value = 1

    result = await service.get_job(user_id=1, job_id=1)

    mock_repo.count_active_public_jobs_by_company.assert_awaited_once()
    assert result.employer_summary is None


async def test_get_job_omits_employer_summary_for_a_blank_company(
    service: JobService, mock_repo: AsyncMock
) -> None:
    from app.models.job import Job

    job = Job(
        id=1,
        fingerprint="abc1234567890def",
        identity_scope="public",
        identity_key=scoped_key("public", "legacy:job-service:blank-company"),
        title="Senior Engineer",
        company="   ",
        location="Manila",
        source="linkedin",
        source_url="https://example.com/job/1",
        raw_response={},
        fetched_at=__import__("datetime").datetime(2026, 1, 1),
        visibility="public",
        ingest_source="scraper",
    )
    mock_repo.get_visible_by_id.return_value = job

    result = await service.get_job(user_id=1, job_id=1)

    mock_repo.count_active_public_jobs_by_company.assert_not_awaited()
    assert result.employer_summary is None


async def test_get_job_raises_not_found(service: JobService, mock_repo: AsyncMock) -> None:
    """get_job raises JobNotFoundError when repo returns None."""
    mock_repo.get_visible_by_id.return_value = None

    with pytest.raises(JobNotFoundError):
        await service.get_job(user_id=1, job_id=999)


@pytest.mark.asyncio
async def test_changed_description_is_forced_into_incremental_embedding_handoff() -> None:
    post = RawJobPost(
        source="indeed",
        source_url="https://example.test/jobs/9",
        title="Engineer",
        company="Acme",
        description="New materially changed description",
    )

    class Source:
        name = "indeed"

        def fetch(self, query: object) -> list[RawJobPost]:
            return [post]

    repository = AsyncMock()
    repository.find_by_alias_identity.return_value = SimpleNamespace(
        identity_key=scoped_key("public", "url:https://example.test/jobs/9"),
        description="Old description",
    )
    repository.find_by_apply_url.return_value = None
    repository.upsert_job.return_value = (9, False)
    repository.attach_source_link = AsyncMock()

    class Coordinator:
        async def ensure_embedding(self, job_id: int, *, force: bool = False) -> object:
            assert job_id == 9
            assert force is True
            return type("Result", (), {"status": "ready"})()

    service = JobService(repository, embedding_coordinator=Coordinator())  # type: ignore[arg-type]
    with patch("app.domain.jobs.service.build_sources", return_value=[Source()]):
        result = await service.ingest(
            IngestRequest(search_term="Engineer"),
            default_sources=["indeed"],
            default_country="USA",
            default_results=10,
            proxies=None,
        )

    assert result.embedded_job_ids == [9]
