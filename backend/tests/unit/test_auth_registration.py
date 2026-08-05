"""Registration security regressions for OAuth and password accounts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.api.deps import get_app_settings, get_auth_service, get_optional_redis
from app.core.config import Settings
from app.domain.auth.exceptions import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
)
from app.domain.auth.repository import UserRepository
from app.domain.auth.service import AuthService
from app.main import app

pytestmark = pytest.mark.unit


def _settings() -> Settings:
    return Settings(
        environment="test",
        jwt_secret=SecretStr("test-secret-for-registration-123456789"),
    )


def _user(*, password: str | None, google_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        email="user@example.com",
        name="Test User",
        hashed_password=password,
        google_id=google_id,
        avatar_url=("https://example.com/avatar.png" if google_id else None),
        is_active=True,
    )


@pytest.mark.asyncio
async def test_public_registration_rejects_google_only_account_without_mutation() -> None:
    repo = AsyncMock(spec=UserRepository)
    existing = _user(password=None, google_id="google-sub-7")
    repo.get_by_email.return_value = existing
    sessions = AsyncMock()
    service = AuthService(repo, _settings(), web_sessions=sessions)

    with pytest.raises(EmailAlreadyRegisteredError):
        await service.register(
            email="user@example.com", name="Attacker", password="attacker-password"
        )

    repo.update_password.assert_not_awaited()
    repo.create.assert_not_awaited()
    sessions.create_session.assert_not_awaited()
    assert existing.hashed_password is None
    assert existing.google_id == "google-sub-7"
    assert existing.avatar_url == "https://example.com/avatar.png"


@pytest.mark.asyncio
async def test_public_registration_rejects_existing_password_account() -> None:
    repo = AsyncMock(spec=UserRepository)
    repo.get_by_email.return_value = _user(password="existing-hash")
    service = AuthService(repo, _settings())

    with pytest.raises(EmailAlreadyRegisteredError):
        await service.register(
            email="user@example.com", name="Another User", password="new-password"
        )

    repo.update_password.assert_not_awaited()
    repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_registration_rejects_dual_provider_account() -> None:
    repo = AsyncMock(spec=UserRepository)
    repo.get_by_email.return_value = _user(
        password="existing-hash", google_id="google-sub-7"
    )
    service = AuthService(repo, _settings())

    with pytest.raises(EmailAlreadyRegisteredError):
        await service.register(
            email="user@example.com", name="Another User", password="new-password"
        )

    repo.update_password.assert_not_awaited()
    repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_registration_attempts_never_mutate_or_create_sessions() -> None:
    repo = AsyncMock(spec=UserRepository)
    existing = _user(password=None, google_id="google-sub-7")
    repo.get_by_email.return_value = existing
    sessions = AsyncMock()
    service = AuthService(repo, _settings(), web_sessions=sessions)

    for _ in range(2):
        with pytest.raises(EmailAlreadyRegisteredError):
            await service.register(
                email="user@example.com", name="Attacker", password="new-password"
            )

    repo.update_password.assert_not_awaited()
    repo.create.assert_not_awaited()
    sessions.create_session.assert_not_awaited()
    assert existing.hashed_password is None
    assert existing.google_id == "google-sub-7"
    assert existing.avatar_url == "https://example.com/avatar.png"


@pytest.mark.asyncio
async def test_new_email_registration_still_creates_account_and_session() -> None:
    repo = AsyncMock(spec=UserRepository)
    created = _user(password="new-hash")
    repo.get_by_email.return_value = None
    repo.create.return_value = created
    sessions = AsyncMock()
    service = AuthService(repo, _settings(), web_sessions=sessions)

    tokens = await service.register(
        email="new@example.com", name="New User", password="new-password"
    )

    assert tokens.user.id == created.id
    repo.create.assert_awaited_once_with(
        email="new@example.com", name="New User", hashed_password=ANY
    )
    sessions.create_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejected_password_cannot_authenticate_google_only_account() -> None:
    repo = AsyncMock(spec=UserRepository)
    repo.get_by_email.return_value = _user(password=None, google_id="google-sub-7")
    service = AuthService(repo, _settings())

    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(
            email="user@example.com", password="attacker-password"
        )


@pytest.mark.asyncio
async def test_google_only_account_can_still_sign_in_with_google(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = AsyncMock(spec=UserRepository)
    existing = _user(password=None, google_id="google-sub-7")
    repo.get_by_google_id.return_value = existing
    sessions = AsyncMock()
    settings = _settings()
    settings.google_client_id = "google-client-id"
    service = AuthService(repo, settings, web_sessions=sessions)

    monkeypatch.setattr(
        "app.domain.auth.service.google_id_token.verify_oauth2_token",
        lambda *args, **kwargs: {
            "sub": "google-sub-7",
            "email": "user@example.com",
            "email_verified": True,
            "name": "Test User",
        },
    )

    tokens = await service.google_auth(id_token_str="valid-google-token")

    assert tokens.user.id == existing.id
    repo.get_by_email.assert_not_awaited()
    repo.link_google.assert_not_awaited()
    repo.update_password.assert_not_awaited()
    sessions.create_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_api_returns_conflict_without_token_or_cookie() -> None:
    repo = AsyncMock(spec=UserRepository)
    repo.get_by_email.return_value = _user(password=None, google_id="google-sub-7")
    service = AuthService(repo, _settings())

    original_overrides = app.dependency_overrides.copy()
    class _Redis:
        async def eval(self, *args: object) -> list[int]:
            return [1, 3600]

    app.dependency_overrides[get_auth_service] = lambda: service
    app.dependency_overrides[get_app_settings] = _settings
    app.dependency_overrides[get_optional_redis] = lambda: _Redis()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "  USER@Example.com ",
                    "name": "Attacker",
                    "password": "attacker-password",
                },
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Unable to create an account with this email. Please sign in or use Google."
    }
    assert "access_token" not in response.json()
    assert "refresh_token" not in response.cookies
    assert "set-cookie" not in response.headers
    repo.update_password.assert_not_awaited()
