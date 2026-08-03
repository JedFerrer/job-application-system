"""Password-reset hardening regression tests.

Verifies that the reset token is never returned in non-development API
responses (audit finding #4).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.domain.auth.repository import UserRepository
from app.domain.auth.service import AuthService

pytestmark = pytest.mark.unit


def _settings(environment: str = "development") -> Settings:
    """Return a Settings instance with the given environment."""
    return Settings(
        environment=environment,
        jwt_secret=SecretStr("test-secret-for-testing-12345678"),
    )


async def test_dev_includes_reset_url() -> None:
    """In development, forgot_password includes reset_url in the response."""
    repo = AsyncMock(spec=UserRepository)
    user = AsyncMock()
    user.id = 1
    user.hashed_password = "some-hash"
    repo.get_by_email.return_value = user
    repo.advance_password_reset_version.return_value = 1

    svc = AuthService(repository=repo, settings=_settings("development"))
    result = await svc.forgot_password(email="user@example.com")

    assert "message" in result
    assert "reset_url" in result
    assert result["reset_url"].startswith("/reset-password?token=")


async def test_production_excludes_reset_url() -> None:
    """In production, forgot_password does NOT include reset_url."""
    repo = AsyncMock(spec=UserRepository)
    user = AsyncMock()
    user.id = 1
    user.hashed_password = "some-hash"
    repo.get_by_email.return_value = user
    repo.advance_password_reset_version.return_value = 1

    svc = AuthService(repository=repo, settings=_settings("production"))
    result = await svc.forgot_password(email="user@example.com")

    assert "message" in result
    assert "reset_url" not in result


async def test_staging_excludes_reset_url() -> None:
    """In staging, forgot_password also excludes reset_url."""
    repo = AsyncMock(spec=UserRepository)
    user = AsyncMock()
    user.id = 1
    user.hashed_password = "some-hash"
    repo.get_by_email.return_value = user
    repo.advance_password_reset_version.return_value = 1

    svc = AuthService(repository=repo, settings=_settings("staging"))
    result = await svc.forgot_password(email="user@example.com")

    assert "message" in result
    assert "reset_url" not in result


async def test_unknown_email_always_returns_generic_message() -> None:
    """Unknown email returns generic message with no reset_url (no user leak)."""
    repo = AsyncMock(spec=UserRepository)
    repo.get_by_email.return_value = None

    for env in ("development", "staging", "production"):
        svc = AuthService(repository=repo, settings=_settings(env))
        result = await svc.forgot_password(email="nobody@example.com")

        assert result == {
            "message": "If the email is registered, a reset link has been sent."
        }
        assert "reset_url" not in result


async def test_google_only_user_returns_generic_message() -> None:
    """Google-only accounts (no password) get generic message, no reset_url."""
    repo = AsyncMock(spec=UserRepository)
    user = AsyncMock()
    user.id = 2
    user.hashed_password = None  # Google-only account
    repo.get_by_email.return_value = user

    for env in ("development", "staging", "production"):
        svc = AuthService(repository=repo, settings=_settings(env))
        result = await svc.forgot_password(email="googleuser@example.com")

        assert result == {
            "message": "If the email is registered, a reset link has been sent."
        }
        assert "reset_url" not in result
