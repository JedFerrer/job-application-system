"""Unit tests for JobService.get_job."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.jobs.exceptions import JobNotFoundError
from app.domain.jobs.identity import scoped_key
from app.domain.jobs.schemas import IngestRequest, JobResponse
from app.domain.jobs.service import JobService
from app.infrastructure.job_sources.base import RawJobPost, SourceSearchResult

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


@pytest.mark.asyncio
async def test_later_source_persistence_failure_preserves_prior_committed_result() -> None:
    """A failed source cannot erase counters or IDs committed by an earlier one."""
    posts = [
        RawJobPost(
            source=source,
            source_url=f"https://example.test/{source}/1",
            title=f"{source} engineer",
            company="Acme",
        )
        for source in ("first", "second")
    ]

    class Source:
        def __init__(self, post: RawJobPost) -> None:
            self.name = post.source
            self._post = post

        def fetch(self, query: object) -> list[RawJobPost]:
            return [self._post]

    repository = AsyncMock()
    repository.link_public_jobs_batch.return_value = 2
    service = JobService(repository, external_enrichment_enabled=False)

    sources = [Source(post) for post in posts]
    with (
        patch("app.domain.jobs.service.build_sources", return_value=sources),
        patch.object(
            service,
            "_upsert",
            new=AsyncMock(side_effect=[(101, True, False), RuntimeError("database down")]),
        ),
    ):
        result = await service.ingest(
            IngestRequest(search_term="Engineer"),
            default_sources=["first", "second"],
            default_country="USA",
            default_results=10,
            proxies=None,
            subscriber_user_ids=[1, 2],
        )

    assert result.inserted == 1
    assert result.updated == 0
    assert result.touched_job_ids == [101]
    assert result.subscriber_links == 2
    assert [item.status for item in result.source_results] == ["success", "failed"]
    assert [
        (item.inserted, item.updated, item.embedded)
        for item in result.source_results
    ] == [(1, 0, 0), (0, 0, 0)]
    repository.commit.assert_awaited_once()
    repository.rollback.assert_awaited_once()
    repository.link_public_jobs_batch.assert_awaited_once_with(user_ids=[1, 2], job_ids=[101])


@pytest.mark.asyncio
async def test_accepted_posts_from_one_source_share_one_observation_time() -> None:
    """One accepted source result gives all of its sightings one observation time."""
    posts = [
        RawJobPost(
            source="indeed",
            source_url=f"https://example.test/jobs/{index}",
            title="Engineer",
        )
        for index in (1, 2)
    ]

    class Source:
        name = "indeed"

        def fetch(self, query: object) -> list[RawJobPost]:
            return posts

    repository = AsyncMock()
    service = JobService(repository, external_enrichment_enabled=False)
    upsert = AsyncMock(side_effect=[(1, True, False), (2, True, False)])
    with (
        patch("app.domain.jobs.service.build_sources", return_value=[Source()]),
        patch.object(service, "_upsert", new=upsert),
    ):
        await service.ingest(
            IngestRequest(search_term="Engineer"),
            default_sources=["indeed"],
            default_country="USA",
            default_results=10,
            proxies=None,
        )

    observations = [call.kwargs["observed_at"] for call in upsert.await_args_list]
    assert len(observations) == 2
    assert observations[0] == observations[1]
    assert observations[0].tzinfo is UTC


@pytest.mark.asyncio
async def test_failed_source_does_not_create_or_refresh_an_observation() -> None:
    """A provider failure has no accepted sighting to update lifecycle data."""

    class Source:
        name = "indeed"

        def fetch(self, query: object) -> list[RawJobPost]:
            raise TimeoutError("fixture timeout")

    repository = AsyncMock()
    service = JobService(repository, external_enrichment_enabled=False)
    upsert = AsyncMock()
    with (
        patch("app.domain.jobs.service.build_sources", return_value=[Source()]),
        patch.object(service, "_upsert", new=upsert),
    ):
        result = await service.ingest(
            IngestRequest(search_term="Engineer"),
            default_sources=["indeed"],
            default_country="USA",
            default_results=10,
            proxies=None,
        )

    upsert.assert_not_awaited()
    assert result.source_results[0].status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "reason_code"),
    [("failed", "timeout"), ("unavailable", "provider_error")],
)
async def test_non_success_result_never_persists_malformed_posts(
    status: str, reason_code: str
) -> None:
    """Only accepted source results may become observation evidence."""

    class Source:
        name = "indeed"

    repository = AsyncMock()
    service = JobService(repository, external_enrichment_enabled=False)
    upsert = AsyncMock()
    malformed_post = RawJobPost(
        source="indeed",
        source_url="https://example.test/jobs/should-not-persist",
        title="Should not persist",
    )
    with (
        patch("app.domain.jobs.service.build_sources", return_value=[Source()]),
        patch(
            "app.domain.jobs.service.execute_sources",
            new=AsyncMock(
                return_value=[
                    SourceSearchResult(
                        source="indeed",
                        status=status,  # type: ignore[arg-type]
                        reason_code=reason_code,  # type: ignore[arg-type]
                        posts=[malformed_post],
                        fetched=1,
                    )
                ]
            ),
        ),
        patch.object(service, "_upsert", new=upsert),
    ):
        result = await service.ingest(
            IngestRequest(search_term="Engineer"),
            default_sources=["indeed"],
            default_country="USA",
            default_results=10,
            proxies=None,
        )

    upsert.assert_not_awaited()
    assert result.source_results[0].status == status
    assert result.source_results[0].accepted == 0


@pytest.mark.asyncio
async def test_unknown_completeness_empty_result_does_not_create_an_observation() -> None:
    class Source:
        name = "indeed"

    service = JobService(AsyncMock(), external_enrichment_enabled=False)
    upsert = AsyncMock()
    with (
        patch("app.domain.jobs.service.build_sources", return_value=[Source()]),
        patch(
            "app.domain.jobs.service.execute_sources",
            new=AsyncMock(
                return_value=[
                    SourceSearchResult(
                        source="indeed", status="empty", completeness="unknown"
                    )
                ]
            ),
        ),
        patch.object(service, "_upsert", new=upsert),
    ):
        result = await service.ingest(
            IngestRequest(search_term="Engineer"),
            default_sources=["indeed"],
            default_country="USA",
            default_results=10,
            proxies=None,
        )

    upsert.assert_not_awaited()
    assert result.source_results[0].completeness == "unknown"


def test_job_response_derives_observation_freshness_without_changing_lifecycle() -> None:
    now = datetime(2026, 9, 8, tzinfo=UTC)
    job = SimpleNamespace(
        id=1,
        title="Engineer",
        company="Acme",
        location=None,
        is_remote=None,
        job_type=None,
        description=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        source="indeed",
        source_url="https://example.test/jobs/1",
        posted_at=now - timedelta(days=31),
        fetched_at=now - timedelta(days=8),
        shift_type=None,
        company_logo=None,
        visibility="public",
        ingest_source="scraper",
        created_by_user_id=None,
        imported_at=None,
        status="expired",
        first_seen_at=now - timedelta(days=20),
        last_seen_at=now - timedelta(days=8),
        expired_at=now - timedelta(days=1),
    )

    response = JobService._job_response(job, now=now)  # type: ignore[arg-type]

    assert response.status == "expired"
    assert response.not_recently_seen is True
    assert response.first_seen_at == now - timedelta(days=20)
    assert response.last_seen_at == now - timedelta(days=8)


@pytest.mark.parametrize(
    ("last_seen_at", "expected"),
    [
        (datetime(2026, 9, 1, tzinfo=UTC), False),
        (datetime(2026, 9, 1, 0, 0, 1, tzinfo=UTC), False),
        (None, None),
    ],
)
def test_not_recently_seen_uses_a_strict_seven_day_threshold(
    last_seen_at: datetime | None, expected: bool | None
) -> None:
    now = datetime(2026, 9, 8, tzinfo=UTC)
    job = SimpleNamespace(
        id=2,
        title="Engineer",
        company="Acme",
        location=None,
        is_remote=None,
        job_type=None,
        description=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        source="indeed",
        source_url="https://example.test/jobs/2",
        posted_at=None,
        fetched_at=now,
        shift_type=None,
        company_logo=None,
        visibility="public",
        ingest_source="scraper",
        created_by_user_id=None,
        imported_at=None,
        status="active",
        first_seen_at=now - timedelta(days=8),
        last_seen_at=last_seen_at,
        expired_at=None,
    )

    assert JobService._job_response(job, now=now).not_recently_seen == expected  # type: ignore[arg-type]
