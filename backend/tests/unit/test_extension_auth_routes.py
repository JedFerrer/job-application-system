"""Authorization regression tests for extension session endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import (
    ExtensionAuthPrincipal,
    get_current_extension_principal,
    get_current_web_user,
    get_extension_auth_service,
)
from app.api.v1.extension import auth as auth_routes
from app.infrastructure.database import get_db
from app.models.user import User


class _FakeAuthService:
    def __init__(self) -> None:
        self.logout_calls: list[str] = []

    async def logout(self, session_id: str) -> None:
        self.logout_calls.append(session_id)


class _FakeExtensionRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def revoke_owned_session(self, *, session_id: str, user_id: int) -> bool:
        self.calls.append((session_id, user_id))
        return False


def _app(
    *, principal: ExtensionAuthPrincipal, user: User, service: _FakeAuthService
) -> FastAPI:
    app = FastAPI()
    app.include_router(auth_routes.router)
    app.include_router(auth_routes.web_router)

    async def principal_override() -> ExtensionAuthPrincipal:
        return principal

    async def user_override() -> User:
        return user

    async def service_override() -> _FakeAuthService:
        return service

    async def db_override() -> Any:
        yield SimpleNamespace()

    app.dependency_overrides[get_current_extension_principal] = principal_override
    app.dependency_overrides[get_current_web_user] = user_override
    app.dependency_overrides[get_extension_auth_service] = service_override
    app.dependency_overrides[get_db] = db_override
    return app


def test_extension_logout_ignores_caller_selected_session() -> None:
    user = User(id=7, email="a@example.com", name="A", is_active=True)
    service = _FakeAuthService()
    principal = ExtensionAuthPrincipal(user=user, session_id="own-session")
    client = TestClient(_app(principal=principal, user=user, service=service))

    response = client.post(
        "/extension/auth/logout",
        json={"session_id": "other-session"},
        headers={"Authorization": "Bearer valid"},
    )

    assert response.status_code == 204
    assert service.logout_calls == ["own-session"]


def test_dashboard_revoke_route_passes_authenticated_owner(monkeypatch: Any) -> None:
    repo = _FakeExtensionRepository()
    monkeypatch.setattr(auth_routes, "ExtensionRepository", lambda _db: repo)
    user = User(id=7, email="a@example.com", name="A", is_active=True)
    client = TestClient(
        _app(
            principal=ExtensionAuthPrincipal(user=user, session_id="own-session"),
            user=user,
            service=_FakeAuthService(),
        )
    )

    response = client.delete("/extension/sessions/other-session")

    assert response.status_code == 204
    assert repo.calls == [("other-session", 7)]
