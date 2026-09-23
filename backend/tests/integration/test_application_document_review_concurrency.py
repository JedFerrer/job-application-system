"""PostgreSQL admission-race coverage for application document reviews."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.api.deps import (
    get_current_user,
    get_document_review_service,
    get_task_store,
)
from app.api.v1 import applications as applications_module
from app.core.config import get_settings
from app.domain.applications.document_review import DocumentReviewService
from app.domain.applications.schemas import DocumentReviewQueuedResponse
from app.infrastructure.database import get_db
from app.infrastructure.task_store import TaskStore
from app.models.application_package import ApplicationPackage
from app.models.application_preparation import ApplicationPreparation
from app.models.application_review import ApplicationDocumentReview
from app.models.user import User
from tests.unit.test_application_preparation import _seed

pytestmark = pytest.mark.integration


class _TaskStore(TaskStore):
    def __init__(self) -> None:
        self.created: list[tuple[str, int, str]] = []

    async def create(self, task_type: str, user_id: int) -> str:
        task_id = f"task-{len(self.created) + 1}"
        self.created.append((task_type, user_id, task_id))
        return task_id

    async def update(self, task_id: str, **fields: Any) -> None:
        return None


class _CoordinatedReviewService(DocumentReviewService):
    def __init__(self, session: AsyncSession, barrier: asyncio.Barrier) -> None:
        super().__init__(session)
        self._barrier = barrier
        self._admission_read_complete = False

    async def _current_owned_package(
        self, *, user_id: int, application_id: int, package_id: int
    ) -> ApplicationPackage:
        # The race fixture isolates active-row recovery; current-package admission
        # is covered by direct service tests with a fully prepared application.
        return await self._owned_package(
            user_id=user_id, application_id=application_id, package_id=package_id
        )

    async def _active(
        self, *, user_id: int, application_id: int, package: ApplicationPackage
    ) -> ApplicationDocumentReview | None:
        active = await super()._active(
            user_id=user_id, application_id=application_id, package=package
        )
        if not self._admission_read_complete:
            self._admission_read_complete = True
            await self._barrier.wait()
        return active


class _EndpointReviewService(DocumentReviewService):
    """Use the concurrency fixture's reviewable package without rebuilding preparation."""

    async def _current_owned_package(
        self, *, user_id: int, application_id: int, package_id: int
    ) -> ApplicationPackage:
        return await self._owned_package(
            user_id=user_id, application_id=application_id, package_id=package_id
        )


@pytest.fixture(scope="module")
def engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


@pytest.fixture(autouse=True)
async def clean_db(engine: AsyncEngine) -> None:
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        await session.execute(text("TRUNCATE TABLE users CASCADE"))
        await session.commit()


async def _seed_package(session: AsyncSession) -> tuple[int, int]:
    application = await _seed(session)
    content: dict[str, object] = {
        "resume": {
            "content": {"summary": "Built APIs"},
            "provenance": {"source_revision_sha256": "a"},
        },
        "cover_letter": {"content": "Built APIs for this role"},
    }
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    package = ApplicationPackage(
        application_id=application.id,
        revision=1,
        content=content,
        content_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
        schema_version="application-package-v1",
        idempotency_key="package",
    )
    session.add(package)
    await session.flush()
    assert package.id is not None
    session.add(
        ApplicationPreparation(
            application_id=application.id, reviewable_package_id=package.id
        )
    )
    await session.commit()
    return application.id, package.id


async def test_concurrent_review_triggers_recover_the_active_review_race(
    engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        application_id, package_id = await _seed_package(session)
    barrier = asyncio.Barrier(2)
    store = _TaskStore()

    async def trigger(key: str) -> DocumentReviewQueuedResponse:
        async with factory() as session:
            service = _CoordinatedReviewService(session, barrier)
            try:
                result = await service.start(
                    user_id=1,
                    application_id=application_id,
                    package_id=package_id,
                    idempotency_key=key,
                    rerun=False,
                    task_store=store,
                )
                assert isinstance(result, DocumentReviewQueuedResponse)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise

    first, second = await asyncio.wait_for(
        asyncio.gather(trigger("first"), trigger("second")), timeout=10
    )
    assert sorted([first.existing, second.existing]) == [False, True]
    assert first.review_id == second.review_id
    assert first.task_id == second.task_id
    assert store.created == [("document_review", 1, first.task_id)]
    async with factory() as session:
        active_count = await session.scalar(
            select(func.count()).select_from(ApplicationDocumentReview).where(
                ApplicationDocumentReview.user_id == 1,
                ApplicationDocumentReview.package_id == package_id,
                ApplicationDocumentReview.state.in_(("queued", "running")),
            )
        )
        assert active_count == 1


async def test_review_endpoint_commits_before_a_separate_background_session_reads_review(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A request-created review is visible when its BackgroundTask begins."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        application_id, package_id = await _seed_package(session)
    store = _TaskStore()
    observed: list[str] = []

    async def db_dependency() -> Any:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def current_user() -> User:
        return User(id=1, email="owner@example.test", name="Owner")

    def review_service(
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> DocumentReviewService:
        return _EndpointReviewService(db)

    async def worker(*, task_id: str, review_id: str, user_id: int) -> None:
        async with factory() as session:
            review = await session.scalar(
                select(ApplicationDocumentReview).where(
                    ApplicationDocumentReview.id == review_id,
                    ApplicationDocumentReview.user_id == user_id,
                )
            )
            assert review is not None
            assert review.state == "queued"
            assert review.task_id == task_id
            review.state = "running"
            await session.commit()
        observed.append(review_id)

    app = FastAPI()
    app.include_router(applications_module.router)

    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_document_review_service] = review_service
    app.dependency_overrides[get_task_store] = lambda: store
    monkeypatch.setattr(applications_module, "run_document_review_background", worker)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/applications/{application_id}/packages/{package_id}/reviews",
            headers={"Idempotency-Key": "request-transaction-boundary"},
            json={},
        )

    assert response.status_code == 202
    result = response.json()
    assert observed == [result["review_id"]]
    assert store.created == [("document_review", 1, result["task_id"])]
    async with factory() as session:
        review = await session.get(ApplicationDocumentReview, result["review_id"])
        assert review is not None
        assert review.state == "running"
