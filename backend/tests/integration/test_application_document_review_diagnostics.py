"""PostgreSQL persistence coverage for durable document-review diagnostics."""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.api.deps import get_current_user
from app.api.v1 import applications as applications_module
from app.core.config import get_settings
from app.domain.applications import document_review
from app.domain.applications.review_prompts import REVIEW_POLICY_VERSION
from app.domain.applications.schemas import DocumentReviewQueuedResponse, DocumentReviewResponse
from app.infrastructure.database import get_db
from app.infrastructure.llm_client import LLMUnavailableError
from app.infrastructure.task_store import TaskStore
from app.models.application_package import ApplicationPackage
from app.models.application_review import ApplicationDocumentReview
from app.models.user import User
from tests.unit.test_application_preparation import _seed

pytestmark = pytest.mark.integration


class _Store(TaskStore):
    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []
        self.created: list[tuple[str, int, str]] = []

    async def create(self, task_type: str, user_id: int) -> str:
        task_id = f"task-{len(self.created) + 1}"
        self.created.append((task_type, user_id, task_id))
        return task_id

    async def update(self, task_id: str, **fields: Any) -> None:
        self.updates.append({"task_id": task_id, **fields})


class _Logger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def warning(self, event_name: str, **fields: object) -> None:
        self.events.append((event_name, fields))


def _config() -> Config:
    from pathlib import Path

    return Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))


def _content() -> dict[str, object]:
    return {
        "job": {"description": "Requires Python"},
        "resume": {"content": {"summary": "Built APIs"}, "provenance": {"source_revision_sha256": "a"}},
        "cover_letter": {"content": "Built APIs for this role"},
    }


def _finding(*, locator: str = "/resume/content/summary", excerpt: str = "Built APIs") -> dict[str, object]:
    return {
        "id": "f1", "document": "resume", "category": "repetition", "severity": "suggestion",
        "excerpt": excerpt, "locator": locator, "rationale": "Repeated wording",
        "evidence": [{"role": "target", "document": "resume", "locator": locator, "excerpt": excerpt}],
        "proposed_action": "Vary the wording",
    }


def _provider_finding(*, evidence_id: str = "resume-e0001") -> dict[str, object]:
    return {
        "id": "f1", "category": "repetition", "severity": "suggestion",
        "rationale": "Repeated wording", "target_evidence_id": evidence_id,
        "proposed_action": "Vary the wording",
    }


@pytest.fixture(scope="module")
def engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


@pytest.fixture(autouse=True)
async def clean_db(engine: AsyncEngine) -> None:
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        await session.execute(text("TRUNCATE TABLE users CASCADE"))
        await session.commit()


async def _seed_review(session: AsyncSession, review_id: str) -> ApplicationDocumentReview:
    application = await _seed(session)
    content = _content()
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    package = ApplicationPackage(
        application_id=application.id, revision=1, content=content,
        content_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
        schema_version="application-package-v1", idempotency_key=f"package-{review_id}",
    )
    session.add(package)
    await session.flush()
    review = ApplicationDocumentReview(
        id=review_id, user_id=1, application_id=application.id, package_id=package.id,
        package_content_sha256=package.content_sha256, review_policy_version=REVIEW_POLICY_VERSION,
        idempotency_key=f"review-{review_id}", task_id=f"task-{review_id}", state="queued", findings=[],
    )
    session.add(review)
    await session.commit()
    return review


async def _reloaded(engine: AsyncEngine, review_id: str) -> ApplicationDocumentReview:
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        review = await session.scalar(select(ApplicationDocumentReview).where(ApplicationDocumentReview.id == review_id))
        assert review is not None
        return review


def _install_worker(
    monkeypatch: pytest.MonkeyPatch, engine: AsyncEngine, provider: object, store: _Store, logger: _Logger,
) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(document_review, "async_session_factory", factory)
    monkeypatch.setattr(document_review, "llm_chat_completion", provider)
    monkeypatch.setattr(document_review, "TaskStore", lambda _redis: store)
    monkeypatch.setattr(document_review, "get_redis", lambda: object())
    monkeypatch.setattr(document_review, "logger", logger)


class _ReviewService(document_review.DocumentReviewService):
    """Exercise cache behavior without rebuilding preparation fixtures."""

    async def _current_owned_package(
        self, *, user_id: int, application_id: int, package_id: int,
    ) -> ApplicationPackage:
        return await self._owned_package(
            user_id=user_id, application_id=application_id, package_id=package_id,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("review_id", "responses", "expected_state", "expected_generations", "expected_failure_reason"),
    [
        ("provider-failure", [LLMUnavailableError("SECRET_PROVIDER_RESPONSE", provider_failure_category="rate_limit", providers_exhausted=True, attempted_provider_names=("openai",), attempt_categories=("rate_limit",))], "failed", 1, None),
        ("noneligible", [{"findings": [_finding(locator="/resume/content/summary/extra")] }], "failed", 1, None),
        ("namespace-gen2-failure", [
            {"findings": [_provider_finding(evidence_id="job-e0001")]},
            {"findings": [_provider_finding(evidence_id="job-e0001")]},
        ], "failed", 2, None),
        ("gen2-failure", [{"findings": [_provider_finding(evidence_id="resume-e999999")]}, LLMUnavailableError("SECRET_PROVIDER_RESPONSE", provider_failure_category="rate_limit", providers_exhausted=True, attempted_provider_names=("openai",), attempt_categories=("rate_limit",))], "failed", 2, "invalid_evidence_id"),
        ("schema-gen2-failure", [{}, {}], "failed", 2, None),
        ("gen1-success", [{"findings": [_provider_finding()]}], "completed", 1, None),
        ("gen2-success", [{"findings": [_provider_finding(evidence_id="resume-e999999")]}, {"findings": [_provider_finding()]}], "completed", 2, None),
    ],
)
async def test_diagnostic_jsonb_persists_and_reloads(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, review_id: str, responses: list[object],
    expected_state: str, expected_generations: int, expected_failure_reason: str | None,
) -> None:
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        review = await _seed_review(session, review_id)
    semantic_calls = 0
    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal semantic_calls
        item = responses[semantic_calls]
        semantic_calls += 1
        if isinstance(item, BaseException):
            raise item
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(item)))]), "fake-model"
    store, logger = _Store(), _Logger()
    _install_worker(monkeypatch, engine, provider, store, logger)
    await document_review.run_document_review_background(task_id=review.task_id, review_id=review.id, user_id=1)
    persisted = await _reloaded(engine, review.id)
    assert persisted.state == expected_state
    assert persisted.diagnostic_summary is not None
    summary = persisted.diagnostic_summary
    assert summary["diagnostic_version"] == "document-review-diagnostic-v1"
    assert summary["review_id"] == review.id and summary["task_id"] == review.task_id
    assert summary["outcome"] == ("completed" if expected_state == "completed" else "failed")
    if expected_state == "completed":
        assert summary["generation_count"] == expected_generations
        assert summary["completed_on_generation"] == expected_generations
        assert semantic_calls == expected_generations
        assert "findings" not in summary and "evidence" not in summary
        finding = persisted.findings[0]
        evidence_items = finding["evidence"]
        assert isinstance(evidence_items, list)
        evidence = evidence_items[0]
        assert finding["document"] == "resume"
        assert finding["locator"] == "/resume/content/summary"
        assert finding["excerpt"] == "Built APIs"
        assert evidence == {
            "role": "target",
            "document": "resume",
            "locator": "/resume/content/summary",
            "excerpt": "Built APIs",
        }
        assert "evidence_id" not in json.dumps(persisted.findings)
        if review_id == "gen2-success":
            assert "resume-e999999" not in json.dumps(persisted.findings)
    else:
        generations = summary["generations"]
        assert isinstance(generations, list) and len(generations) == expected_generations, logger.events
        assert [entry["generation_attempt"] for entry in generations] == list(range(1, expected_generations + 1))
        assert all(isinstance(entry["generation_attempt"], int) for entry in generations)
        if expected_failure_reason is not None:
            assert expected_failure_reason in [entry.get("reason") for entry in generations]
        if review_id == "noneligible":
            assert semantic_calls == 1
    rendered = json.dumps(summary, sort_keys=True)
    for sentinel in ("SECRET_RAW_LOCATOR", "SECRET_EXCERPT", "SECRET_FINDING_TEXT", "SECRET_PROVIDER_RESPONSE", "SECRET_EXCEPTION_TEXT"):
        assert sentinel not in rendered
    if review_id == "schema-gen2-failure":
        schema_generations = cast(list[dict[str, object]], summary["generations"])
        assert "response_schema_validation" in [entry.get("stage") for entry in schema_generations]
        assert "schema_issue_type" not in rendered and "field_path" not in rendered
    assert store.updates[-1]["status"] == ("done" if expected_state == "completed" else "failed")


@pytest.mark.asyncio
async def test_v10_namespace_schema_correction_persists_gen2_completion_safely(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        review = await _seed_review(session, "namespace-schema-gen2-success")
    invalid_evidence_id = "resume-e0001"
    responses = [
        {"findings": [{
            "id": "namespace-correction", "category": "cross_document_mismatch",
            "severity": "suggestion", "rationale": "Documents conflict",
            "resume_evidence_id": invalid_evidence_id,
            "cover_letter_evidence_id": invalid_evidence_id,
            "proposed_action": "Align the documents",
        }]},
        {"findings": [{
            "id": "namespace-correction", "category": "cross_document_mismatch",
            "severity": "suggestion", "rationale": "Documents conflict",
            "resume_evidence_id": "resume-e0001",
            "cover_letter_evidence_id": "cover-e0001",
            "proposed_action": "Align the documents",
        }]},
    ]
    calls = 0

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        if calls >= 2:
            raise AssertionError("third generation attempted")
        payload = responses[calls]
        calls += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]), "fake-model"

    store, logger = _Store(), _Logger()
    _install_worker(monkeypatch, engine, provider, store, logger)
    await document_review.run_document_review_background(task_id=review.task_id, review_id=review.id, user_id=1)

    persisted = await _reloaded(engine, review.id)
    assert calls == 2
    assert persisted.state == "completed"
    assert store.updates[-1]["status"] == "done"
    assert persisted.diagnostic_summary == {
        "diagnostic_version": "document-review-diagnostic-v1", "review_id": review.id,
        "task_id": review.task_id, "outcome": "completed", "generation_count": 2,
        "completed_on_generation": 2,
    }
    rendered = json.dumps((persisted.diagnostic_summary, logger.events), sort_keys=True)
    assert "document_review_schema_validation_failed" in rendered
    for forbidden in (invalid_evidence_id, "cover-e0001", "regex", "pattern", "input", "locator", "excerpt", "rationale"):
        assert forbidden not in rendered
    assert "evidence_id" not in json.dumps(persisted.findings)
    schema_events = [fields for event_name, fields in logger.events if event_name == "document_review_schema_validation_failed"]
    assert schema_events == [{
        "generation": 1,
        "validation_error_count": 1,
        "normalized_schema_issues": ({
            "schema_issue_type": "invalid_evidence_id_namespace",
            "field_path": "findings.*.cover_letter_evidence_id",
        },),
    }]


class _ForcedCanonicalFinding(BaseModel):
    model_config = ConfigDict(extra="ignore")
    excerpt: str = Field(max_length=1)


@pytest.mark.asyncio
async def test_v8_canonical_validation_diagnostic_persists_safely(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        review = await _seed_review(session, "canonical-validation")
    calls = 0

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        calls += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"findings": [_provider_finding()]})))]), "fake-model"

    store, logger = _Store(), _Logger()
    monkeypatch.setattr(document_review, "DocumentReviewFinding", _ForcedCanonicalFinding)
    _install_worker(monkeypatch, engine, provider, store, logger)
    await document_review.run_document_review_background(task_id=review.task_id, review_id=review.id, user_id=1)

    persisted = await _reloaded(engine, review.id)
    assert calls == 1 and persisted.state == "failed"
    assert persisted.diagnostic_summary == {
        "diagnostic_version": "document-review-diagnostic-v1", "review_id": review.id,
        "task_id": review.task_id, "outcome": "failed",
        "generations": [{"generation_attempt": 1, "stage": "canonical_validation"}],
    }
    canonical_events = [fields for event_name, fields in logger.events if event_name == "document_review_canonical_validation_failed"]
    schema_events = [fields for event_name, fields in logger.events if event_name == "document_review_schema_validation_failed"]
    assert canonical_events == [{"generation": 1, "exception_class": "ValidationError", "validation_error_count": 1}]
    assert schema_events == []
    rendered = json.dumps((persisted.diagnostic_summary, logger.events), sort_keys=True)
    for forbidden in ("excerpt", "locator", "evidence_id", "normalized_schema_issues", "Built APIs"):
        assert forbidden not in rendered


@pytest.mark.asyncio
async def test_diagnostic_specific_persistence_failure_rolls_back_and_preserves_failed_terminal_state(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        review = await _seed_review(session, "fallback-failed")
    fail_once = {"value": True}
    def reject_diagnostic(_session: Session, _context: object, instances: object) -> None:
        del instances
        if fail_once["value"] and any(
            isinstance(item, ApplicationDocumentReview) and item.diagnostic_summary is not None
            for item in _session.dirty
        ):
            fail_once["value"] = False
            raise RuntimeError("SECRET_DIAGNOSTIC_DB_EXCEPTION_DO_NOT_LOG")
    event.listen(Session, "before_flush", reject_diagnostic)
    store, logger = _Store(), _Logger()
    async def provider(**_kwargs: object) -> tuple[Any, str]:
        raise LLMUnavailableError("SECRET_PROVIDER_RESPONSE", provider_failure_category="rate_limit", providers_exhausted=True, attempted_provider_names=("openai",), attempt_categories=("rate_limit",))
    _install_worker(monkeypatch, engine, provider, store, logger)
    try:
        await document_review.run_document_review_background(task_id=review.task_id, review_id=review.id, user_id=1)
    finally:
        event.remove(Session, "before_flush", reject_diagnostic)
    persisted = await _reloaded(engine, review.id)
    assert fail_once["value"] is False
    assert persisted.state == "failed"
    assert persisted.failure_code == "unavailable"
    assert persisted.failure_message == "Review unavailable"
    assert persisted.diagnostic_summary is None
    terminal = [entry for entry in store.updates if entry.get("status") == "failed"]
    assert len(terminal) == 1
    assert ("document_review.diagnostic_persistence_failed", {}) in logger.events
    assert "SECRET_DIAGNOSTIC_DB_EXCEPTION_DO_NOT_LOG" not in str(logger.events)


@pytest.mark.asyncio
async def test_historical_null_and_public_projection_do_not_fabricate_or_expose_diagnostics(
    engine: AsyncEngine,
) -> None:
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        review = await _seed_review(session, "historical-null")
        review.state = "failed"
        review.outcome = "insufficient_evidence"
        review.failure_code = "unavailable"
        review.failure_message = "Review unavailable"
        await session.commit()
    historical = await _reloaded(engine, review.id)
    assert historical.diagnostic_summary is None

    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        persisted_review = await session.get(ApplicationDocumentReview, review.id)
        assert persisted_review is not None
        generation = document_review._generation_summary(
            generation_attempt=1,
            stage="evidence_validation",
            reason="invalid_evidence_id",
            structural_metadata={
                "document": "resume",
                "unknown_diagnostic_key": "SECRET_UNKNOWN_DIAGNOSTIC_VALUE",
            },
        )
        persisted_review.diagnostic_summary = document_review._failure_diagnostic(
            persisted_review, persisted_review.task_id, [generation],
        )
        await session.commit()
    durable = await _reloaded(engine, review.id)
    assert durable.diagnostic_summary is not None
    rendered_durable = json.dumps(durable.diagnostic_summary, sort_keys=True)
    assert "unknown_diagnostic_key" not in rendered_durable
    assert "SECRET_UNKNOWN_DIAGNOSTIC_VALUE" not in rendered_durable

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async def db() -> Any:
        async with factory() as session:
            yield session
    async def user() -> User:
        return User(id=1, email="owner@example.test", name="Owner")
    app = FastAPI()
    app.include_router(applications_module.router)
    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_current_user] = user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/applications/{review.application_id}/packages/{review.package_id}/reviews/{review.id}"
        )
    assert response.status_code == 200
    rendered = response.text
    for key in ("diagnostic_summary", "diagnostic_version", "generations", "generation_attempt", "provider_failure_category", "provider_attempt_count"):
        assert key not in rendered


@pytest.mark.asyncio
async def test_v10_cache_separation_preserves_historical_v9_readability(
    engine: AsyncEngine,
) -> None:
    """A completed v9 row remains readable but cannot satisfy a v10 cache lookup."""
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        historical = await _seed_review(session, "historical-v7")
        historical.review_policy_version = "application-document-review-v9"
        historical.state = "completed"
        historical.outcome = "no_findings"
        historical.completed_at = datetime.now(UTC)
        await session.commit()
        service = _ReviewService(session)
        readable = await service.get(
            user_id=1,
            application_id=historical.application_id,
            package_id=historical.package_id,
            review_id=historical.id,
        )
        assert readable.review_policy_version == "application-document-review-v9"

    store = _Store()
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        service = _ReviewService(session)
        first = await service.start(
            user_id=1,
            application_id=historical.application_id,
            package_id=historical.package_id,
            idempotency_key="normal-v10",
            rerun=False,
            task_store=store,
        )
        assert isinstance(first, DocumentReviewQueuedResponse)
        assert first.existing is False
        v10_review = await session.get(ApplicationDocumentReview, first.review_id)
        assert v10_review is not None
        v10_review.state = "completed"
        v10_review.outcome = "no_findings"
        v10_review.completed_at = datetime.now(UTC)
        await session.commit()

    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        service = _ReviewService(session)
        reused = await service.start(
            user_id=1,
            application_id=historical.application_id,
            package_id=historical.package_id,
            idempotency_key="normal-v10-reuse",
            rerun=False,
            task_store=store,
        )
        assert isinstance(reused, DocumentReviewResponse)
        assert reused.id == first.review_id
        assert reused.review_policy_version == REVIEW_POLICY_VERSION
        assert store.created == [("document_review", 1, first.task_id)]


def test_0042_upgrade_downgrade_reupgrade_preserves_historical_null() -> None:
    config = _config()
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    try:
        command.downgrade(config, "0041")
        with engine.connect() as connection:
            assert "diagnostic_summary" not in {column["name"] for column in inspect(connection).get_columns("application_document_reviews")}
        command.upgrade(config, "0042")
        with engine.connect() as connection:
            column = next(column for column in inspect(connection).get_columns("application_document_reviews") if column["name"] == "diagnostic_summary")
            assert column["type"].__class__.__name__ == "JSONB" and column["nullable"] is True
        command.downgrade(config, "0041")
        with engine.connect() as connection:
            assert "diagnostic_summary" not in {column["name"] for column in inspect(connection).get_columns("application_document_reviews")}
        command.upgrade(config, "0042")
    finally:
        command.upgrade(config, "head")
        engine.dispose()
