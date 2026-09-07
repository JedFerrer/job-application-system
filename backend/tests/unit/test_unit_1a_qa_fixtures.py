"""Safety and determinism coverage for the isolated Unit 1A Browser QA mode."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1 import jobs as jobs_api
from app.core.config import Settings
from app.domain.jobs.schemas import IngestRequest, IngestResponse
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


async def test_isolated_qa_ingest_never_invokes_embedding_or_llm_clients() -> None:
    """The guarded fixture may persist jobs, but may not enrich them externally."""
    repository = AsyncMock()
    repository.get_description_by_fingerprint.return_value = (False, None)
    repository.upsert_job.return_value = (1, True)
    service = JobService(repository, external_enrichment_enabled=False)

    with (
        patch("app.infrastructure.embeddings.embed_text", new=AsyncMock()) as embed,
        patch("app.domain.jobs.service.classify_shift_llm", new=AsyncMock()) as classify,
    ):
        result = await service.ingest(
            IngestRequest(
                search_term="Unit 1A QA",
                country="QA",
                results_wanted=1,
                sources=[QA_MIXED_SUCCESS],
            ),
            default_sources=[],
            default_country="QA",
            default_results=1,
            proxies=None,
            qa_unit_1a_browser=True,
        )

    assert result.inserted == 2
    assert result.embedded == 0
    embed.assert_not_awaited()
    classify.assert_not_awaited()


async def test_background_ingest_uses_guarded_qa_service_without_a_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise POST /jobs/ingest's background composition, not just the registry."""
    import app.domain.jobs.service as job_service_module

    constructed: dict[str, object] = {}

    class FakeStore:
        async def update(self, task_id: str, **fields: object) -> None:
            del task_id, fields

    class FakeSession:
        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    class FakeService:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args
            constructed.update(kwargs)

        async def ingest(self, *args: object, **kwargs: object) -> IngestResponse:
            constructed["ingest_kwargs"] = kwargs
            return IngestResponse(
                fetched=0,
                inserted=0,
                updated=0,
                embedded=0,
                sources=[],
                outcome="success",
            )

    monkeypatch.setattr(jobs_api, "TaskStore", lambda redis: FakeStore())
    monkeypatch.setattr(jobs_api, "get_redis", lambda: object())
    monkeypatch.setattr(
        jobs_api,
        "get_settings",
        lambda: SimpleNamespace(
            user_timezone="Asia/Manila",
            enabled_job_sources=[QA_MIXED_SUCCESS],
            job_search_country="QA",
            job_results_wanted=1,
            proxy_list=[],
            qa_unit_1a_browser_fixtures_enabled=True,
        ),
    )
    monkeypatch.setattr(jobs_api, "async_session_factory", lambda: FakeSession())
    monkeypatch.setattr(
        jobs_api,
        "JobEmbeddingCoordinator",
        lambda *args: (_ for _ in ()).throw(AssertionError("external coordinator was built")),
    )
    monkeypatch.setattr(job_service_module, "JobService", FakeService)

    await jobs_api._run_ingest_bg(
        "task-1",
        IngestRequest(search_term="Unit 1A QA", country="QA", sources=[QA_MIXED_SUCCESS]),
        1,
    )

    assert constructed["embedding_coordinator"] is None
    assert constructed["external_enrichment_enabled"] is False
    ingest_kwargs = constructed["ingest_kwargs"]
    assert isinstance(ingest_kwargs, dict)
    assert ingest_kwargs == {
        "default_sources": [QA_MIXED_SUCCESS],
        "default_country": "QA",
        "default_results": 1,
        "proxies": None,
        "qa_unit_1a_browser": True,
        "progress": ingest_kwargs["progress"],
        "user_id": 1,
    }
