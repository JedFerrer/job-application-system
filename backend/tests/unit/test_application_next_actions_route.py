"""Route contract tests for application next actions."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_application_service, get_current_user
from app.api.v1 import applications as applications_module
from app.core.lifespan import register_exception_handlers
from app.domain.auth.exceptions import InvalidTokenError
from app.domain.applications.schemas import (
    ApplicationListItem,
    ApplicationListResponse,
)
from app.domain.applications.service import ApplicationService
from app.models.user import User

pytestmark = pytest.mark.unit


def _make_user(
    user_id: int = 1, email: str = "test@example.com"
) -> User:
    return User(
        id=user_id,
        email=email,
        name=f"User {user_id}",
        is_active=True,
    )


def _make_response() -> ApplicationListResponse:
    overdue_item = ApplicationListItem(
        id=42,
        job_id=99,
        job_title="Backend Engineer",
        company="Acme",
        location="Remote",
        source_url="https://example.com/jobs/99",
        status="saved",
        cover_letter_id=None,
        notes=None,
        next_action_type="follow_up",
        next_action_note="Ping recruiter after the portfolio review.",
        next_action_due_date=date(2026, 7, 13),
        next_action_completed_at=None,
        created_at=datetime(2026, 7, 13, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 7, 13, 10, 0, tzinfo=UTC),
    )
    today_item = ApplicationListItem(
        id=43,
        job_id=100,
        job_title="Platform Engineer",
        company="Beta",
        location="Remote",
        source_url="https://example.com/jobs/100",
        status="applied",
        cover_letter_id=None,
        notes=None,
        next_action_type="prepare_interview",
        next_action_note="Review system design notes.",
        next_action_due_date=date(2026, 7, 14),
        next_action_completed_at=None,
        created_at=datetime(2026, 7, 13, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 7, 13, 10, 0, tzinfo=UTC),
    )
    return ApplicationListResponse(
        items=[overdue_item, today_item], total=2, limit=2, next_cursor=None
    )


def _build_app(
    *,
    current_user: User | None = None,
    application_service: ApplicationService | None = None,
    invalid_auth: bool = False,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(applications_module.router)

    if current_user is not None:

        async def _override_get_current_user() -> User:
            return current_user

        app.dependency_overrides[get_current_user] = _override_get_current_user
    elif invalid_auth:

        async def _override_invalid_auth() -> User:
            raise InvalidTokenError

        app.dependency_overrides[get_current_user] = _override_invalid_auth

    if application_service is not None:
        app.dependency_overrides[get_application_service] = (
            lambda: application_service
        )

    return app


def test_next_actions_route_returns_current_users_due_actions_and_shape() -> None:
    current_user = _make_user(7, "owner@example.com")
    service = AsyncMock(spec=ApplicationService)
    service.list_due_next_actions = AsyncMock(return_value=_make_response())

    app = _build_app(current_user=current_user, application_service=service)
    client = TestClient(app)

    resp = client.get("/applications/next-actions?due_by=2026-07-14")

    assert resp.status_code == 200, resp.text
    service.list_due_next_actions.assert_awaited_once_with(
        user_id=current_user.id,
        due_by=date(2026, 7, 14),
    )

    data = resp.json()
    assert data["total"] == 2
    assert data["limit"] == 2
    assert data["next_cursor"] is None
    assert len(data["items"]) == 2

    overdue_item = data["items"][0]
    assert overdue_item["job_title"] == "Backend Engineer"
    assert overdue_item["company"] == "Acme"
    assert overdue_item["next_action_type"] == "follow_up"
    assert overdue_item["next_action_due_date"] == "2026-07-13"
    assert overdue_item["next_action_note"] == (
        "Ping recruiter after the portfolio review."
    )

    today_item = data["items"][1]
    assert today_item["job_title"] == "Platform Engineer"
    assert today_item["company"] == "Beta"
    assert today_item["next_action_type"] == "prepare_interview"
    assert today_item["next_action_due_date"] == "2026-07-14"
    assert today_item["next_action_note"] == "Review system design notes."


def test_next_actions_route_requires_authentication() -> None:
    service = AsyncMock(spec=ApplicationService)
    service.list_due_next_actions = AsyncMock(return_value=_make_response())

    app = _build_app(application_service=service, invalid_auth=True)
    client = TestClient(app)

    resp = client.get("/applications/next-actions?due_by=2026-07-14")

    assert resp.status_code == 401
    service.list_due_next_actions.assert_not_awaited()
