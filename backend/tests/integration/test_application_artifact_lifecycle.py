# ruff: noqa: E501, I001
"""Real PostgreSQL and HTTP proofs for retained application artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
import threading
from typing import Any

import pytest
from fastapi import BackgroundTasks, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_current_user, get_task_store
from app.api.v1 import applications as applications_module
from app.core.config import get_settings
from app.domain.applications.artifact_lifecycle import ArtifactLifecycleService
from app.domain.applications import artifact_lifecycle as lifecycle_module
from app.domain.applications.artifacts import ArtifactFailureCategory, ArtifactValidationError, ArtifactValidationReport, VALIDATION_POLICY_VERSION
from app.infrastructure.database import get_db
from app.infrastructure.task_store import TaskStore
from app.models.application_artifact import ApplicationArtifact
from app.models.application_package import ApplicationPackage
from app.models.application_review import ApplicationDocumentReview
from app.models.user import User
from tests.unit.test_application_preparation import _seed

pytestmark = pytest.mark.integration
PDF = b"%PDF-1.4\nartifact-test\n"


class FakeTaskStore(TaskStore):
    def __init__(self) -> None:
        self.created: list[str] = []
        self.data: dict[str, dict[str, Any]] = {}

    async def create(self, task_type: str, user_id: int) -> str:
        task_id = f"task-{len(self.created) + 1}"
        self.created.append(task_id)
        self.data[task_id] = {"type": task_type, "user_id": user_id, "status": "queued"}
        return task_id

    async def update(self, task_id: str, **fields: Any) -> None:
        self.data[task_id].update(fields)

    async def get(self, task_id: str) -> dict[str, Any] | None:
        return self.data.get(task_id)


@pytest.fixture(scope="module")
def engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


@pytest.fixture(autouse=True)
async def clean_db(engine: AsyncEngine) -> None:
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        await session.execute(text("TRUNCATE TABLE users CASCADE"))
        await session.commit()


async def seed_package(session: AsyncSession) -> tuple[int, int]:
    application = await _seed(session)
    content: dict[str, object] = {
        "resume": {"content": {"summary": "Built APIs"}},
        "cover_letter": {"mode": "selected", "content": "Built APIs for this role"},
    }
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    package = ApplicationPackage(
        application_id=application.id, revision=1, content=content,
        content_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
        schema_version="application-package-v1", idempotency_key="artifact-test",
    )
    session.add(package)
    await session.commit()
    return application.id, package.id


def make_app(engine: AsyncEngine, store: FakeTaskStore, user_id: int = 1, fail_commit: bool = False) -> FastAPI:
    class FailingCommitSession(AsyncSession):
        async def commit(self) -> None:
            raise RuntimeError("synthetic database commit failure")

    factory = async_sessionmaker(engine, class_=FailingCommitSession if fail_commit else AsyncSession, expire_on_commit=False)

    async def db_dependency() -> Any:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def current_user() -> User:
        return User(id=user_id, email="owner@example.test", name="Owner")

    app = FastAPI()
    app.include_router(applications_module.router)
    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_task_store] = lambda: store
    return app


def upload_path(application_id: int, package_id: int) -> str:
    return f"/applications/{application_id}/packages/{package_id}/artifacts"


def upload_args(content: bytes = PDF) -> dict[str, Any]:
    return {
        "data": {"document_type": "resume", "format": "pdf", "exporter_version": "browser-v1"},
        "files": {"file": ("../resume.pdf", content, "application/pdf")},
    }


async def test_upload_commit_duplicate_detail_download_and_delete(engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        application_id, package_id = await seed_package(session)
    store = FakeTaskStore()
    observed: list[int] = []

    async def worker(*, artifact_id: int, task_id: str, validation_attempt: int) -> None:
        async with factory() as session:
            row = await session.get(ApplicationArtifact, artifact_id)
            assert row is not None
            assert row.validation_state == "queued"
            assert row.task_id == task_id
            assert row.validation_attempt == validation_attempt == 1
            observed.append(artifact_id)

    monkeypatch.setattr(applications_module, "run_artifact_validation_background", worker)
    async with AsyncClient(transport=ASGITransport(app=make_app(engine, store)), base_url="http://test") as client:
        first = await client.post(upload_path(application_id, package_id), **upload_args())
        assert first.status_code == 202, first.text
        artifact_id = first.json()["artifact"]["id"]
        second = await client.post(upload_path(application_id, package_id), **upload_args())
        assert second.status_code == 200, second.text
        assert second.json()["existing"] is True
        assert second.json()["artifact"]["id"] == artifact_id
        detail = await client.get(f"/applications/{application_id}/artifacts/{artifact_id}")
        assert detail.status_code == 200
        assert "content" not in detail.json()
        assert detail.json()["retry_after"] is not None
        package_response = await client.get(f"/applications/{application_id}/packages/{package_id}")
        assert package_response.status_code == 200
        assert [item["id"] for item in package_response.json()["artifacts"]] == [artifact_id]
        workspace_response = await client.get(f"/applications/{application_id}/workspace")
        assert workspace_response.status_code == 200
        assert workspace_response.json()["package_history"][0]["artifacts"][0]["id"] == artifact_id
        downloaded = await client.get(f"/applications/{application_id}/artifacts/{artifact_id}/download")
        assert downloaded.status_code == 200
        assert downloaded.content == PDF
        assert downloaded.headers["cache-control"] == "private, no-store"
        assert downloaded.headers["x-content-type-options"] == "nosniff"
        assert downloaded.headers["content-disposition"].startswith("attachment;")
        assert ".." not in downloaded.headers["content-disposition"]
        assert "\r" not in downloaded.headers["content-disposition"]
        assert "\n" not in downloaded.headers["content-disposition"]
        assert (await client.delete(f"/applications/{application_id}/artifacts/{artifact_id}")).status_code == 409
    async with factory() as session:
        row = await session.get(ApplicationArtifact, artifact_id)
        assert row is not None
        assert row.content == PDF
        assert hashlib.sha256(downloaded.content).hexdigest() == row.sha256 == hashlib.sha256(PDF).hexdigest()
        assert await session.scalar(select(func.count()).select_from(ApplicationArtifact)) == 1
    assert observed == [artifact_id]
    assert len(store.created) == 1
    async with factory() as session:
        row = await session.get(ApplicationArtifact, artifact_id)
        assert row is not None
        row.validation_state = "validating"
        await session.commit()
    async with AsyncClient(transport=ASGITransport(app=make_app(engine, store)), base_url="http://test") as client:
        assert (await client.delete(f"/applications/{application_id}/artifacts/{artifact_id}")).status_code == 409
    async with factory() as session:
        row = await session.get(ApplicationArtifact, artifact_id)
        assert row is not None
        row.validation_state = "validated"
        session.add(ApplicationDocumentReview(
            user_id=1, application_id=application_id, package_id=package_id,
            package_content_sha256="a" * 64, review_policy_version="test",
            idempotency_key="review-test", task_id="review-test", state="completed",
            outcome="no_findings", findings=[],
        ))
        await session.commit()
    async with AsyncClient(transport=ASGITransport(app=make_app(engine, store)), base_url="http://test") as client:
        assert (await client.delete(f"/applications/{application_id}/artifacts/{artifact_id}")).status_code == 204
    async with factory() as session:
        assert await session.get(ApplicationArtifact, artifact_id) is None
        assert await session.get(ApplicationPackage, package_id) is not None
        assert await session.scalar(select(func.count()).select_from(ApplicationDocumentReview)) == 1


async def test_concurrent_identical_uploads_admit_one_task(engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        application_id, package_id = await seed_package(session)
    store = FakeTaskStore()
    executions: list[int] = []
    schedules: list[int] = []
    a_at_commit = asyncio.Event()
    release_a = asyncio.Event()
    b_lock_attempted = asyncio.Event()
    first_task: asyncio.Task[Any] | None = None
    second_task: asyncio.Task[Any] | None = None

    class HeldCommitSession(AsyncSession):
        async def commit(self) -> None:
            if asyncio.current_task() is first_task:
                a_at_commit.set()
                await release_a.wait()
            await super().commit()

    held_factory = async_sessionmaker(engine, class_=HeldCommitSession, expire_on_commit=False)

    async def db_dependency() -> Any:
        async with held_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    def observe_b_lock(_connection: object, _cursor: object, statement: str, _parameters: object, _context: object, _executemany: bool) -> None:
        query = statement.casefold()
        if asyncio.current_task() is second_task and "for update" in query and "from applications" in query:
            b_lock_attempted.set()

    original_add_task = BackgroundTasks.add_task

    def record_schedule(self: BackgroundTasks, func: Any, *args: Any, **kwargs: Any) -> None:
        if func is worker:
            schedules.append(kwargs["artifact_id"])
        original_add_task(self, func, *args, **kwargs)

    async def worker(*, artifact_id: int, task_id: str, validation_attempt: int) -> None:
        executions.append(artifact_id)

    monkeypatch.setattr(applications_module, "run_artifact_validation_background", worker)
    monkeypatch.setattr(BackgroundTasks, "add_task", record_schedule)
    app = make_app(engine, store)
    app.dependency_overrides[get_db] = db_dependency
    event.listen(engine.sync_engine, "before_cursor_execute", observe_b_lock)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first_task = asyncio.create_task(client.post(upload_path(application_id, package_id), **upload_args()))
            await asyncio.wait_for(a_at_commit.wait(), 5)
            second_task = asyncio.create_task(client.post(upload_path(application_id, package_id), **upload_args()))
            await asyncio.wait_for(b_lock_attempted.wait(), 5)
            assert not second_task.done()  # B issued the lock query while A still owns the lock.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(second_task), 0.25)
            assert not second_task.done()
            release_a.set()
            first = await asyncio.wait_for(first_task, 5)
            second = await asyncio.wait_for(second_task, 5)
    finally:
        release_a.set()
        event.remove(engine.sync_engine, "before_cursor_execute", observe_b_lock)
        for task in (first_task, second_task):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    assert b_lock_attempted.is_set()
    assert first.status_code == 202 and first.json()["existing"] is False
    assert second.status_code == 200 and second.json()["existing"] is True
    assert sorted([first.status_code, second.status_code]) == [200, 202]
    assert first.json()["artifact"]["id"] == second.json()["artifact"]["id"]
    assert sorted([first.json()["existing"], second.json()["existing"]]) == [False, True]
    assert len(store.created) == 1
    assert len(schedules) == 1
    assert len(executions) == 1
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(ApplicationArtifact)) == 1
        assert await session.scalar(select(func.sum(ApplicationArtifact.byte_length))) == len(PDF)


async def test_owned_privacy_preflight_and_retry(engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        application_id, package_id = await seed_package(session)
    store = FakeTaskStore()

    async def worker(*, artifact_id: int, task_id: str, validation_attempt: int) -> None:
        return None

    monkeypatch.setattr(applications_module, "run_artifact_validation_background", worker)
    async with AsyncClient(transport=ASGITransport(app=make_app(engine, store)), base_url="http://test") as client:
        assert (await client.post(upload_path(application_id, package_id), **upload_args(b"x"))).status_code == 415
        assert (await client.post(upload_path(application_id, package_id), **upload_args(b""))).status_code == 422
        assert (await client.post(
            upload_path(application_id, package_id),
            data={"document_type": "resume", "format": "docx", "exporter_version": "v1"},
            files={"file": ("x.docx", b"PK\x03\x04bad", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )).status_code == 422
        assert (await client.post(upload_path(application_id, package_id), **upload_args(b"%PDF-" + b"x" * (5 * 1024 * 1024)))).status_code == 413
        assert (await client.post(
            upload_path(application_id, package_id),
            data={"document_type": "wrong", "format": "pdf", "exporter_version": "v1"},
            files={"file": ("x.pdf", PDF, "application/pdf")},
        )).status_code == 415
        created = await client.post(upload_path(application_id, package_id), **upload_args())
        assert created.status_code == 202
        artifact_id = created.json()["artifact"]["id"]
        assert (await client.post(f"/applications/{application_id}/artifacts/{artifact_id}/retry")).status_code == 409
        assert (await client.get(f"/applications/{application_id + 1}/artifacts/{artifact_id}")).status_code == 404
        assert (await client.get(f"/applications/{application_id + 1}/artifacts/{artifact_id}/download")).status_code == 404
        store.data.clear()
        detail_without_task = await client.get(f"/applications/{application_id}/artifacts/{artifact_id}")
        assert detail_without_task.status_code == 200
        assert detail_without_task.json()["validation_state"] == "queued"
    async with AsyncClient(transport=ASGITransport(app=make_app(engine, store, user_id=2)), base_url="http://test") as client:
        paths = [
            ("post", upload_path(application_id, package_id), upload_args()),
            ("get", f"/applications/{application_id}/artifacts/{artifact_id}", {}),
            ("get", f"/applications/{application_id}/artifacts/{artifact_id}/download", {}),
            ("post", f"/applications/{application_id}/artifacts/{artifact_id}/retry", {}),
            ("delete", f"/applications/{application_id}/artifacts/{artifact_id}", {}),
        ]
        for method, path, kwargs in paths:
            assert (await client.request(method.upper(), path, **kwargs)).status_code == 404
    async with factory() as session:
        row = await session.get(ApplicationArtifact, artifact_id)
        assert row is not None
        row.validation_state = "failed"
        row.failure_category = "content_mismatch"
        row.validation_report = {"policy_version": VALIDATION_POLICY_VERSION, "format": "pdf", "state": "failed", "checks": [], "failure_category": "content_mismatch"}
        await session.commit()
    async with AsyncClient(transport=ASGITransport(app=make_app(engine, store)), base_url="http://test") as client:
        duplicate = await client.post(upload_path(application_id, package_id), **upload_args())
        assert duplicate.status_code == 200
        assert duplicate.json()["artifact"]["validation_state"] == "failed"
        assert len(store.created) == 1
        failed_download = await client.get(f"/applications/{application_id}/artifacts/{artifact_id}/download")
        assert failed_download.status_code == 200
        assert failed_download.content == PDF
        retried = await client.post(f"/applications/{application_id}/artifacts/{artifact_id}/retry")
        assert retried.status_code == 202, retried.text
        assert retried.json()["artifact"]["validation_state"] == "queued"
        assert retried.json()["artifact"]["validation_report"] is None
        assert retried.json()["artifact"]["task_id"] != created.json()["artifact"]["task_id"]
    async with factory() as session:
        row = await session.get(ApplicationArtifact, artifact_id)
        assert row is not None
        assert row.validation_attempt == 2
        assert row.failure_category is None


async def test_task_admission_failure_rolls_back_upload(engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        application_id, package_id = await seed_package(session)

    class FailingTaskStore(FakeTaskStore):
        async def create(self, task_type: str, user_id: int) -> str:
            raise RuntimeError("redis down")

    scheduled: list[int] = []

    async def worker(*, artifact_id: int, task_id: str, validation_attempt: int) -> None:
        scheduled.append(artifact_id)

    monkeypatch.setattr(applications_module, "run_artifact_validation_background", worker)
    async with AsyncClient(transport=ASGITransport(app=make_app(engine, FailingTaskStore())), base_url="http://test") as client:
        failed = await client.post(upload_path(application_id, package_id), **upload_args())
        assert failed.status_code == 503
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(ApplicationArtifact)) == 0
    assert scheduled == []


async def test_application_quota_returns_413_without_new_task(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        application_id, package_id = await seed_package(session)
        for index in range(5):
            content = b"%PDF-" + bytes([index]) * (5 * 1024 * 1024 - 5)
            session.add(ApplicationArtifact(
                user_id=1, application_id=application_id, package_id=package_id,
                document_type="resume", format="pdf", filename="resume.pdf",
                content_type="application/pdf", byte_length=len(content),
                sha256=hashlib.sha256(content).hexdigest(), exporter_version="v1",
                expected_text_sha256="a" * 64, validation_policy_version=VALIDATION_POLICY_VERSION,
                validation_state="validated", validation_report={}, content=content,
            ))
        await session.commit()
    store = FakeTaskStore()
    async with AsyncClient(transport=ASGITransport(app=make_app(engine, store)), base_url="http://test") as client:
        response = await client.post(upload_path(application_id, package_id), **upload_args())
        assert response.status_code == 413
        assert response.json()["detail"] == "quota_exceeded"
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(ApplicationArtifact)) == 5
        assert await session.scalar(select(func.sum(ApplicationArtifact.byte_length))) == 25 * 1024 * 1024
    assert store.created == []


async def test_database_commit_failure_does_not_schedule_worker(engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        application_id, package_id = await seed_package(session)
    store = FakeTaskStore()
    scheduled: list[int] = []

    async def worker(*, artifact_id: int, task_id: str, validation_attempt: int) -> None:
        scheduled.append(artifact_id)

    monkeypatch.setattr(applications_module, "run_artifact_validation_background", worker)
    async with AsyncClient(
        transport=ASGITransport(app=make_app(engine, store, fail_commit=True), raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        failed = await client.post(upload_path(application_id, package_id), **upload_args())
        assert failed.status_code == 500
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(ApplicationArtifact)) == 0
    assert len(store.created) == 1  # A short-lived Redis orphan is safe.
    assert scheduled == []


async def test_0043_style_failed_artifact_can_retry(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        application_id, package_id = await seed_package(session)
        store = FakeTaskStore()
        result, _ = await ArtifactLifecycleService(session).create(
            user_id=1, application_id=application_id, package_id=package_id,
            document_type="resume", format="pdf", filename="resume.pdf", content=PDF,
            exporter_version="browser-v1", task_store=store,
        )
        await session.commit()
        row = await session.get(ApplicationArtifact, result.artifact.id)
        assert row is not None
        row.task_id = None
        row.validation_attempt = 0
        row.validation_state = "failed"
        await session.commit()
    async with factory() as session:
        retried, attempt = await ArtifactLifecycleService(session).retry(
            user_id=1, application_id=application_id, artifact_id=result.artifact.id,
            task_store=store,
        )
        await session.commit()
        assert attempt == 1
        assert retried.artifact.task_id is not None


async def test_worker_fences_old_attempt_and_persists_current_report(engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        application_id, package_id = await seed_package(session)
        service = ArtifactLifecycleService(session)
        store = FakeTaskStore()
        result, created = await service.create(
            user_id=1, application_id=application_id, package_id=package_id,
            document_type="resume", format="pdf", filename="resume.pdf", content=PDF,
            exporter_version="browser-v1", task_store=store,
        )
        assert created
        await session.commit()
        artifact_id = result.artifact.id
        first_task = result.artifact.task_id
        assert first_task is not None
    monkeypatch.setattr(lifecycle_module, "async_session_factory", factory)
    monkeypatch.setattr(lifecycle_module, "get_redis", lambda: object())
    monkeypatch.setattr(lifecycle_module, "TaskStore", lambda _redis: store)
    started = asyncio.Event()

    def delayed_validator(**_kwargs: Any) -> ArtifactValidationReport:
        loop.call_soon_threadsafe(started.set)
        blocking_finish.wait(5)
        return ArtifactValidationReport(policy_version=VALIDATION_POLICY_VERSION, format="pdf", state="failed", checks=[], failure_category="content_mismatch")

    loop = asyncio.get_running_loop()
    blocking_finish = threading.Event()
    monkeypatch.setattr(lifecycle_module, "validate_in_isolated_process", delayed_validator)
    first_worker = asyncio.create_task(lifecycle_module.run_artifact_validation_background(
        artifact_id=artifact_id, task_id=first_task, validation_attempt=1,
    ))
    await asyncio.wait_for(started.wait(), 5)
    async with factory() as session:
        row = await session.get(ApplicationArtifact, artifact_id)
        assert row is not None
        assert row.validation_state == "validating"
        row.validation_state_changed_at = datetime.now(UTC) - timedelta(seconds=61)
        await session.commit()
    async with factory() as session:
        retried, attempt = await ArtifactLifecycleService(session).retry(
            user_id=1, application_id=application_id, artifact_id=artifact_id, task_store=store,
        )
        assert attempt == 2
        await session.commit()
    async with factory() as session:
        row = await session.get(ApplicationArtifact, artifact_id)
        assert row is not None
        row.validation_state = "validated"
        row.validation_report = {"policy_version": VALIDATION_POLICY_VERSION, "format": "pdf", "state": "validated", "checks": []}
        await session.commit()
    blocking_finish.set()
    await asyncio.wait_for(first_worker, 5)
    async with factory() as session:
        row = await session.get(ApplicationArtifact, artifact_id)
        assert row is not None
        assert row.validation_attempt == 2
        assert row.task_id == retried.artifact.task_id
        assert row.validation_state == "validated"
        assert row.validation_report["state"] == "validated"


async def test_worker_releases_connection_while_parser_waits(engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        application_id, package_id = await seed_package(session)
        store = FakeTaskStore()
        result, _ = await ArtifactLifecycleService(session).create(
            user_id=1, application_id=application_id, package_id=package_id,
            document_type="resume", format="pdf", filename="resume.pdf", content=PDF,
            exporter_version="browser-v1", task_store=store,
        )
        await session.commit()
        artifact_id = result.artifact.id
        task_id = result.artifact.task_id
        assert task_id is not None
    monkeypatch.setattr(lifecycle_module, "async_session_factory", factory)
    monkeypatch.setattr(lifecycle_module, "get_redis", lambda: object())
    monkeypatch.setattr(lifecycle_module, "TaskStore", lambda _redis: store)
    checked_out: set[int] = set()

    def on_checkout(connection: object, _record: object, _proxy: object) -> None:
        checked_out.add(id(connection))

    def on_checkin(connection: object, _record: object) -> None:
        checked_out.discard(id(connection))

    event.listen(engine.sync_engine, "checkout", on_checkout)
    event.listen(engine.sync_engine, "checkin", on_checkin)
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def paused_parser(**kwargs: Any) -> ArtifactValidationReport:
        assert kwargs["content"] == PDF
        assert isinstance(kwargs["expected"], str)
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        return ArtifactValidationReport(policy_version=VALIDATION_POLICY_VERSION, format="pdf", state="validated", checks=[])

    monkeypatch.setattr(lifecycle_module, "validate_in_isolated_process", paused_parser)
    worker_task = asyncio.create_task(lifecycle_module.run_artifact_validation_background(
        artifact_id=artifact_id, task_id=task_id, validation_attempt=1,
    ))
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert checked_out == set()  # Claim and snapshot sessions returned their connections.
        async with factory() as unrelated:
            assert await asyncio.wait_for(unrelated.scalar(text("SELECT 1")), 2) == 1
        assert checked_out == set()
    finally:
        release.set()
        await asyncio.wait_for(worker_task, 5)
        event.remove(engine.sync_engine, "checkout", on_checkout)
        event.remove(engine.sync_engine, "checkin", on_checkin)
    async with factory() as session:
        row = await session.get(ApplicationArtifact, artifact_id)
        assert row is not None and row.validation_state == "validated"


async def test_worker_terminal_save_failure_keeps_database_authoritative(engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        application_id, package_id = await seed_package(session)
        store = FakeTaskStore()
        result, _ = await ArtifactLifecycleService(session).create(
            user_id=1, application_id=application_id, package_id=package_id,
            document_type="resume", format="pdf", filename="resume.pdf", content=PDF,
            exporter_version="browser-v1", task_store=store,
        )
        await session.commit()
        artifact_id = result.artifact.id
        task_id = result.artifact.task_id
        assert task_id is not None

    class TerminalFailSession(AsyncSession):
        async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
            if str(statement).startswith("UPDATE application_artifacts") and "validation_report" in str(statement):
                raise RuntimeError("synthetic terminal persistence failure")
            return await super().execute(statement, *args, **kwargs)

    monkeypatch.setattr(lifecycle_module, "async_session_factory", async_sessionmaker(engine, class_=TerminalFailSession, expire_on_commit=False))
    monkeypatch.setattr(lifecycle_module, "get_redis", lambda: object())
    monkeypatch.setattr(lifecycle_module, "TaskStore", lambda _redis: store)
    monkeypatch.setattr(lifecycle_module, "validate_in_isolated_process", lambda **_kwargs: ArtifactValidationReport(
        policy_version=VALIDATION_POLICY_VERSION, format="pdf", state="validated", checks=[],
    ))
    await lifecycle_module.run_artifact_validation_background(artifact_id=artifact_id, task_id=task_id, validation_attempt=1)
    async with factory() as session:
        row = await session.get(ApplicationArtifact, artifact_id)
        assert row is not None and row.validation_state == "validating"
        assert row.validation_report == {}
    assert store.data[task_id]["status"] == "failed"
    assert store.data[task_id]["message"] == "Artifact result could not be saved; check artifact status before retrying"


async def test_package_summary_query_excludes_artifact_content(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        application_id, package_id = await seed_package(session)
        store = FakeTaskStore()
        await ArtifactLifecycleService(session).create(
            user_id=1, application_id=application_id, package_id=package_id,
            document_type="resume", format="pdf", filename="resume.pdf", content=PDF,
            exporter_version="browser-v1", task_store=store,
        )
        await session.commit()
    from app.domain.applications.repository import ApplicationRepository

    statements: list[str] = []

    def inspect_summary_sql(_connection: object, _cursor: object, statement: str, _parameters: object, _context: object, _executemany: bool) -> None:
        if "FROM application_artifacts" in statement:
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", inspect_summary_sql)
    try:
        async with factory() as session:
            rows = await ApplicationRepository(session).artifacts_for_packages(user_id=1, package_ids={package_id})
            assert rows[package_id][0].byte_length == len(PDF)
            assert "content" in rows[package_id][0].__mapper__.attrs
            assert "content" not in rows[package_id][0].__dict__
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", inspect_summary_sql)
    assert len(statements) == 1
    assert "application_artifacts.content" not in statements[0]


@pytest.mark.parametrize("initial_state,age_seconds,expected", [
    ("queued", 30, False), ("queued", 61, True),
    ("validating", 30, False), ("validating", 61, True),
    ("failed", 0, True), ("unavailable", 0, True),
    ("validated", 61, False), ("warning", 61, False),
])
async def test_retry_eligibility(engine: AsyncEngine, initial_state: str, age_seconds: int, expected: bool) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        application_id, package_id = await seed_package(session)
        store = FakeTaskStore()
        result, _ = await ArtifactLifecycleService(session).create(
            user_id=1, application_id=application_id, package_id=package_id,
            document_type="resume", format="pdf", filename="resume.pdf", content=PDF,
            exporter_version="browser-v1", task_store=store,
        )
        await session.commit()
        artifact_id = result.artifact.id
    async with factory() as session:
        row = await session.get(ApplicationArtifact, artifact_id)
        assert row is not None
        row.validation_state = initial_state
        row.validation_state_changed_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
        await session.commit()
    async with factory() as session:
        service = ArtifactLifecycleService(session)
        if expected:
            retried, attempt = await service.retry(user_id=1, application_id=application_id, artifact_id=artifact_id, task_store=store)
            await session.commit()
            assert attempt == 2
            assert retried.artifact.validation_state == "queued"
            assert retried.artifact.failure_category is None
            assert len(store.created) == 2
        else:
            from app.domain.applications.artifact_lifecycle import ArtifactConflictError
            with pytest.raises(ArtifactConflictError):
                await service.retry(user_id=1, application_id=application_id, artifact_id=artifact_id, task_store=store)


@pytest.mark.parametrize("outcome,state,category", [
    ("validated", "validated", None),
    ("warning", "warning", None),
    ("parser_timeout", "failed", "parser_timeout"),
    ("parser_unavailable", "unavailable", "parser_unavailable"),
    ("content_mismatch", "failed", "content_mismatch"),
    ("unexpected", "unavailable", "parser_unavailable"),
])
async def test_worker_terminal_outcomes(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, outcome: str, state: str, category: str | None,
) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        application_id, package_id = await seed_package(session)
        store = FakeTaskStore()
        result, _ = await ArtifactLifecycleService(session).create(
            user_id=1, application_id=application_id, package_id=package_id,
            document_type="resume", format="pdf", filename="resume.pdf", content=PDF,
            exporter_version="browser-v1", task_store=store,
        )
        await session.commit()
        artifact_id = result.artifact.id
        task_id = result.artifact.task_id
        assert task_id is not None
    monkeypatch.setattr(lifecycle_module, "async_session_factory", factory)
    monkeypatch.setattr(lifecycle_module, "get_redis", lambda: object())
    monkeypatch.setattr(lifecycle_module, "TaskStore", lambda _redis: store)

    def validator(**kwargs: Any) -> ArtifactValidationReport:
        assert kwargs["content"] == PDF
        if outcome == "unexpected":
            raise RuntimeError("SECRET RAW EXCEPTION")
        if outcome in {"parser_timeout", "parser_unavailable", "content_mismatch"}:
            raise ArtifactValidationError(ArtifactFailureCategory(outcome))
        return ArtifactValidationReport(
            policy_version=VALIDATION_POLICY_VERSION, format="pdf", state=outcome,
            checks=[],
        )

    monkeypatch.setattr(lifecycle_module, "validate_in_isolated_process", validator)
    await lifecycle_module.run_artifact_validation_background(
        artifact_id=artifact_id, task_id=task_id, validation_attempt=1,
    )
    async with factory() as session:
        row = await session.get(ApplicationArtifact, artifact_id)
        assert row is not None
        assert row.validation_state == state
        assert row.failure_category == category
        assert row.validation_report["state"] == state
        assert "SECRET" not in str(row.validation_report)
        assert row.validation_state_changed_at >= row.created_at
    assert store.data[task_id]["status"] == ("done" if state in {"validated", "warning"} else "failed")
