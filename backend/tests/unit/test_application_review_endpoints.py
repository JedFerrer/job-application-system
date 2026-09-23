"""HTTP ownership/error mapping and safe response boundary for Unit 3A reviews."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_document_review_service, get_task_store
from app.api.v1 import applications as applications_module
from app.domain.applications.document_review import ReviewConflictError, ReviewNotFoundError
from app.domain.applications.schemas import DocumentReviewQueuedResponse, DocumentReviewResponse
from app.infrastructure.database import get_db
from app.models.user import User


class _Reviews:
    async def start(
        self, **kwargs: object
    ) -> DocumentReviewResponse | DocumentReviewQueuedResponse:
        if kwargs["package_id"] == 404:
            raise ReviewNotFoundError()
        if kwargs["package_id"] == 409:
            raise ReviewConflictError("conflict")
        if kwargs["package_id"] == 201:
            return DocumentReviewResponse(
                id="review", application_id=1, package_id=201,
                package_content_sha256="a" * 64,
                review_policy_version="application-document-review-v1",
                state="completed", outcome="insufficient_evidence", findings=[],
                failure_code=None, created_at=datetime.now(UTC), completed_at=datetime.now(UTC),
            )
        if kwargs["package_id"] == 200:
            return DocumentReviewQueuedResponse(
                task_id="new-task", review_id="new-review", state="queued", existing=False
            )
        return DocumentReviewQueuedResponse(
            task_id="task", review_id="review", state="queued", existing=True
        )

    async def acknowledge(self, **kwargs: object) -> DocumentReviewResponse:
        if kwargs["package_id"] == 404:
            raise ReviewNotFoundError()
        if kwargs["package_id"] == 409:
            raise ReviewConflictError("Review no longer matches this package")
        return DocumentReviewResponse(
            id="review", application_id=1, package_id=2,
            package_content_sha256="a" * 64,
            review_policy_version="application-document-review-v1",
            state="failed", outcome="insufficient_evidence", findings=[],
            failure_code="unavailable", created_at=datetime.now(UTC), completed_at=None,
        )


class _Tasks:
    async def get(self, task_id: str) -> dict[str, object] | None:
        return {"user_id": 1, "status": "done", "message": "done"} if task_id == "owned" else None


class _Session:
    commits = 0

    async def commit(self) -> None:
        self.__class__.commits += 1


async def _db() -> AsyncGenerator[_Session]:
    yield _Session()


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(applications_module.router)
    async def user() -> User: return User(id=1, email="owner@example.test", name="Owner")
    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[get_document_review_service] = _Reviews
    app.dependency_overrides[get_task_store] = _Tasks
    app.dependency_overrides[get_db] = _db
    return app


def test_review_create_hides_missing_package_and_maps_conflict() -> None:
    client = TestClient(_app())
    headers = {"Idempotency-Key": "key"}
    missing = client.post("/applications/1/packages/404/reviews", headers=headers, json={})
    conflict = client.post("/applications/1/packages/409/reviews", headers=headers, json={})
    assert missing.status_code == 404
    assert conflict.status_code == 409


def test_review_task_is_owner_scoped() -> None:
    client = TestClient(_app())
    assert client.get("/applications/tasks/owned").status_code == 200
    assert client.get("/applications/tasks/missing").status_code == 404


def test_only_new_review_commits_then_schedules_one_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, int]] = []

    async def worker(*, task_id: str, review_id: str, user_id: int) -> None:
        assert _Session.commits == 1
        calls.append((task_id, review_id, user_id))

    _Session.commits = 0
    monkeypatch.setattr(applications_module, "run_document_review_background", worker)
    client = TestClient(_app())
    headers = {"Idempotency-Key": "key"}
    new = client.post("/applications/1/packages/200/reviews", headers=headers, json={})
    replay = client.post("/applications/1/packages/2/reviews", headers=headers, json={})
    cached = client.post("/applications/1/packages/201/reviews", headers=headers, json={})
    assert new.status_code == 202
    assert replay.status_code == 202
    assert cached.status_code == 200
    assert calls == [("new-task", "new-review", 1)]


def test_acknowledgment_hides_foreign_review_maps_conflict_and_returns_safe_fields() -> None:
    client = TestClient(_app())
    body = {
        "package_content_sha256": "a" * 64,
        "acknowledgments": [{"finding_id": "finding", "disposition": "keep_wording"}],
    }

    foreign = client.post("/applications/1/packages/404/reviews/review/acknowledgments", json=body)
    conflict = client.post("/applications/1/packages/409/reviews/review/acknowledgments", json=body)
    valid = client.post("/applications/1/packages/2/reviews/review/acknowledgments", json=body)

    assert foreign.status_code == 404
    assert conflict.status_code == 409
    assert valid.status_code == 200
    forbidden = {"raw_provider_response", "raw_prompt", "package_text", "failure_message"}
    assert forbidden.isdisjoint(valid.json())
