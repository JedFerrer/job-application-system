"""HTTP contract tests for the authenticated search-readiness endpoint."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db
from app.api.v1 import dashboard as dashboard_module
from app.core.lifespan import register_exception_handlers
from app.domain.dashboard.search_readiness import SearchReadinessResponse
from app.models.user import User

pytestmark = pytest.mark.unit


def _app(*, user: User | None = None) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(dashboard_module.router)
    app.dependency_overrides[get_db] = lambda: object()
    if user is not None:
        async def current_user() -> User:
            return user

        app.dependency_overrides[get_current_user] = current_user
    return app


def _response() -> SearchReadinessResponse:
    return SearchReadinessResponse.model_validate(
        {
            "overall_state": "almost_ready",
            "checks": {
                "resume": {"state": "draft_review", "has_pending_draft": True},
                "profile": {"state": "needs_setup"},
                "preferences": {"state": "ready"},
            },
            "next_action": {
                "reason": "draft_review",
                "label": "Review resume draft",
            },
        }
    )


def test_authentication_is_required() -> None:
    response = TestClient(_app()).get("/dashboard/search-readiness")

    assert response.status_code == 401


def test_endpoint_is_current_user_scoped_and_compact(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    class FakeService:
        def __init__(self, *_repositories: object) -> None:
            pass

        async def get(self, user_id: int) -> SearchReadinessResponse:
            calls.append(user_id)
            return _response()

    monkeypatch.setattr(dashboard_module, "SearchReadinessService", FakeService)
    response = TestClient(_app(user=User(id=42, email="user@test.com", name="User"))).get(
        "/dashboard/search-readiness"
    )

    assert response.status_code == 200
    assert calls == [42]
    assert response.json() == {
        "overall_state": "almost_ready",
        "checks": {
            "resume": {"state": "draft_review", "has_pending_draft": True},
            "profile": {"state": "needs_setup"},
            "preferences": {"state": "ready"},
        },
        "next_action": {
            "reason": "draft_review",
            "label": "Review resume draft",
        },
    }
    body = response.text
    assert all(secret not in body for secret in ("resume_id", "draft_id", "filename", "job_titles"))


def test_inconsistent_repository_state_returns_generic_availability_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingService:
        def __init__(self, *_repositories: object) -> None:
            pass

        async def get(self, user_id: int) -> SearchReadinessResponse:
            del user_id
            raise ValueError("private invariant detail")

    monkeypatch.setattr(dashboard_module, "SearchReadinessService", FailingService)
    response = TestClient(_app(user=User(id=42, email="user@test.com", name="User"))).get(
        "/dashboard/search-readiness"
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Search readiness is temporarily unavailable."
    }
    assert "private invariant detail" not in response.text
