"""Preference-refresh admission endpoint contract tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_preference_service, get_task_store
from app.api.v1 import preferences as preferences_routes
from app.core.lifespan import register_exception_handlers
from app.domain.preferences.exceptions import RefreshCooldownError
from app.infrastructure.database import get_db
from app.models.user import User

pytestmark = pytest.mark.unit


class FakeDb:
    """Tracks the explicit commit used for ambiguous publish failures."""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class FakeTaskStore:
    def __init__(self) -> None:
        self.created: list[tuple[str, int]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []

    async def create(self, task_type: str, user_id: int) -> str:
        self.created.append((task_type, user_id))
        return f"preference-refresh-{len(self.created)}"

    async def update(self, task_id: str, **fields: Any) -> None:
        self.updated.append((task_id, fields))


class FakePreferenceService:
    def __init__(self) -> None:
        self.calls = 0

    async def prepare_refresh(self, user_id: int) -> None:
        self.calls += 1
        if self.calls > 1:
            raise RefreshCooldownError(retry_after_seconds=3599)


def _app(
    service: FakePreferenceService,
    store: FakeTaskStore,
    db: FakeDb,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(preferences_routes.router)

    async def current_user() -> User:
        return User(id=7, email="preference@example.test", name="Preference User", is_active=True)

    async def database() -> Any:
        yield db

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_preference_service] = lambda: service
    app.dependency_overrides[get_task_store] = lambda: store
    app.dependency_overrides[get_db] = database
    return app


def test_refresh_accepts_once_then_rejects_without_second_parent_or_dispatch() -> None:
    service = FakePreferenceService()
    store = FakeTaskStore()
    db = FakeDb()
    delayed = MagicMock()

    with patch("app.workers.job_ingestion.scrape_user_on_demand.delay", delayed):
        client = TestClient(_app(service, store, db))
        first = client.post("/preferences/refresh")
        second = client.post("/preferences/refresh")

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.headers["retry-after"] == "3599"
    assert store.created == [("preference_refresh", 7)]
    delayed.assert_called_once_with(7, "preference-refresh-1")


def test_force_query_parameter_does_not_bypass_preference_refresh_cooldown() -> None:
    service = FakePreferenceService()
    store = FakeTaskStore()
    db = FakeDb()
    delayed = MagicMock()

    with patch("app.workers.job_ingestion.scrape_user_on_demand.delay", delayed):
        client = TestClient(_app(service, store, db))
        assert client.post("/preferences/refresh").status_code == 202
        forced_repeat = client.post("/preferences/refresh?force=true")

    assert forced_repeat.status_code == 429
    assert store.created == [("preference_refresh", 7)]
    delayed.assert_called_once_with(7, "preference-refresh-1")


@pytest.mark.parametrize("terminal_status", ["done", "failed", "incomplete"])
def test_terminal_parent_does_not_reopen_preference_refresh_admission(
    terminal_status: str,
) -> None:
    service = FakePreferenceService()
    store = FakeTaskStore()
    db = FakeDb()
    delayed = MagicMock()

    with patch("app.workers.job_ingestion.scrape_user_on_demand.delay", delayed):
        client = TestClient(_app(service, store, db))
        assert client.post("/preferences/refresh").status_code == 202
        store.updated.append(("preference-refresh-1", {"status": terminal_status}))
        repeat = client.post("/preferences/refresh")

    assert repeat.status_code == 429
    assert store.created == [("preference_refresh", 7)]
    delayed.assert_called_once_with(7, "preference-refresh-1")


def test_ambiguous_publish_failure_retains_claim_and_marks_parent_failed() -> None:
    service = FakePreferenceService()
    store = FakeTaskStore()
    db = FakeDb()

    with patch(
        "app.workers.job_ingestion.scrape_user_on_demand.delay",
        side_effect=RuntimeError("broker acknowledgement lost"),
    ):
        response = TestClient(_app(service, store, db)).post("/preferences/refresh")

    assert response.status_code == 503
    assert store.created == [("preference_refresh", 7)]
    assert store.updated == [
        (
            "preference-refresh-1",
            {
                "status": "failed",
                "message": "Refresh dispatch could not be confirmed",
                "error": "Refresh dispatch could not be confirmed",
            },
        )
    ]
    assert db.commits == 1
