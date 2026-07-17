"""Endpoint contract tests for /matches cursor compatibility."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_match_service
from app.api.v1 import matches as matches_module
from app.core.lifespan import register_exception_handlers
from app.models.user import User

pytestmark = pytest.mark.unit


def _build_app_with_overrides(*, current_user: User, match_service: object) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(matches_module.router)

    async def _override_get_current_user() -> User:
        return current_user

    app.dependency_overrides[get_current_user] = _override_get_current_user
    app.dependency_overrides[get_match_service] = lambda: match_service  # type: ignore[assignment]
    return app


class _FailingMatchService:
    async def list_matches(self, **kwargs):  # noqa: ANN001
        assert kwargs["cursor"] == "bad-cursor"
        raise ValueError("bad cursor")


class _EmptyMatchService:
    async def list_matches(self, **kwargs):  # noqa: ANN001
        return type(
            "Resp",
            (),
            {
                "items": [],
                "total": 0,
                "limit": kwargs["limit"],
                "offset": 0 if kwargs.get("offset") is None else kwargs["offset"],
                "next_cursor": None,
                "has_more": False,
            },
        )()


class _SummaryMatchService:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def get_match_summary(self, *, user_id: int, job_id: int) -> None:
        self.calls.append((user_id, job_id))
        return None


def test_invalid_cursor_returns_400() -> None:
    user = User(id=1, email="a@test.com", name="A", is_active=True)
    app = _build_app_with_overrides(current_user=user, match_service=_FailingMatchService())
    client = TestClient(app)

    resp = client.get("/matches?cursor=bad-cursor")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid cursor"


def test_offsetless_first_page_still_works() -> None:
    user = User(id=1, email="a@test.com", name="A", is_active=True)
    app = _build_app_with_overrides(current_user=user, match_service=_EmptyMatchService())
    client = TestClient(app)

    resp = client.get("/matches")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_offset_query_param_is_deprecated_in_openapi() -> None:
    user = User(id=1, email="a@test.com", name="A", is_active=True)
    app = _build_app_with_overrides(current_user=user, match_service=_EmptyMatchService())
    client = TestClient(app)

    schema = client.get("/openapi.json").json()
    params = schema["paths"]["/matches"]["get"]["parameters"]
    offset_param = next(param for param in params if param["name"] == "offset")
    assert offset_param["deprecated"] is True


def test_deprecated_offset_query_param_still_works_temporarily() -> None:
    user = User(id=1, email="a@test.com", name="A", is_active=True)
    app = _build_app_with_overrides(current_user=user, match_service=_EmptyMatchService())
    client = TestClient(app)

    resp = client.get("/matches?offset=20")
    assert resp.status_code == 200
    assert resp.json()["offset"] == 20


def test_match_readiness_is_documented_in_openapi() -> None:
    user = User(id=1, email="a@test.com", name="A", is_active=True)
    app = _build_app_with_overrides(current_user=user, match_service=_EmptyMatchService())
    client = TestClient(app)

    schema = client.get("/openapi.json").json()
    match_item = schema["components"]["schemas"]["MatchItem"]["properties"]
    explanation = schema["components"]["schemas"]["MatchExplanation"]["properties"]

    assert "readiness" in match_item
    assert "readiness" in explanation


def test_match_summary_returns_200_null_and_uses_current_user_scope() -> None:
    user = User(id=42, email="scoped@test.com", name="Scoped", is_active=True)
    service = _SummaryMatchService()
    app = _build_app_with_overrides(current_user=user, match_service=service)
    client = TestClient(app)

    response = client.get("/matches/501/summary")

    assert response.status_code == 200
    assert response.json() is None
    assert service.calls == [(42, 501)]
