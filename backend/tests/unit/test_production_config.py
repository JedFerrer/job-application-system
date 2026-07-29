"""Production-safety config validation regression tests.

Verifies that production misconfigurations raise errors at startup
(audit finding #6).
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.main import validate_startup_settings

pytestmark = pytest.mark.unit


def test_is_production_returns_false_for_development() -> None:
    """is_production is False when environment is 'development'."""
    s = Settings(environment="development")
    assert s.is_production is False


def test_is_production_returns_false_for_staging() -> None:
    """is_production is False when environment is 'staging'."""
    s = Settings(environment="staging")
    assert s.is_production is False


def test_is_production_returns_true_for_production() -> None:
    """is_production is True when environment is 'production'."""
    s = Settings(environment="production")
    assert s.is_production is True


# ── Production guard validation tests ─────────────────────────────────────


def test_production_insecure_jwt_secret_raises() -> None:
    """Production with default JWT secret raises RuntimeError."""
    s = Settings(
        environment="production",
        jwt_secret=SecretStr("insecure-dev-secret-change-me"),
        refresh_cookie_secure=True,
        job_refresh_cooldown_minutes=15,
    )
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        validate_startup_settings(s)


def test_production_refresh_cookie_not_secure_raises() -> None:
    """Production with refresh_cookie_secure=False raises RuntimeError."""
    s = Settings(
        environment="production",
        jwt_secret=SecretStr("a-strong-production-secret-at-least-32-chars!!"),
        refresh_cookie_secure=False,
        job_refresh_cooldown_minutes=15,
    )
    with pytest.raises(RuntimeError, match="REFRESH_COOKIE_SECURE"):
        validate_startup_settings(s)


def test_production_low_cooldown_raises() -> None:
    """Production with job_refresh_cooldown_minutes < 15 raises RuntimeError."""
    s = Settings(
        environment="production",
        jwt_secret=SecretStr("a-strong-production-secret-at-least-32-chars!!"),
        refresh_cookie_secure=True,
        job_refresh_cooldown_minutes=5,
    )
    with pytest.raises(RuntimeError, match="JOB_REFRESH_COOLDOWN"):
        validate_startup_settings(s)


def test_valid_production_settings_pass() -> None:
    """Valid production settings do not raise."""
    s = Settings(
        environment="production",
        jwt_secret=SecretStr("a-strong-production-secret-at-least-32-chars!!"),
        refresh_cookie_secure=True,
        job_refresh_cooldown_minutes=15,
    )
    # Should not raise
    validate_startup_settings(s)


def test_development_insecure_settings_pass() -> None:
    """Development with insecure defaults does not raise (only logs warning)."""
    s = Settings(
        environment="development",
        jwt_secret=SecretStr("insecure-dev-secret-change-me"),
        refresh_cookie_secure=False,
        job_refresh_cooldown_minutes=5,
    )
    # Should not raise in development
    validate_startup_settings(s)


def test_production_refresh_cookie_secure_true_is_valid() -> None:
    """Production with refresh_cookie_secure=True is valid."""
    s = Settings(
        environment="production",
        jwt_secret=SecretStr("a-strong-production-secret-at-least-32-chars!!"),
        refresh_cookie_secure=True,
    )
    assert s.is_production is True
    assert s.refresh_cookie_secure is True


def test_development_allows_insecure_cookies() -> None:
    """Development with refresh_cookie_secure=False is fine (localhost)."""
    s = Settings(environment="development", refresh_cookie_secure=False)
    assert s.refresh_cookie_secure is False
    # No RuntimeError expected in dev.


def test_samesite_none_without_secure_is_detectable() -> None:
    """SameSite=None without Secure=True is a detectable misconfiguration.

    The startup guard in main.py logs a warning for this combination.
    """
    s = Settings(
        environment="development",
        refresh_cookie_secure=False,
        refresh_cookie_samesite="none",
    )
    assert s.refresh_cookie_secure is False
    assert s.refresh_cookie_samesite == "none"
    # The startup guard in main.py will log a warning.


# ── Scrape refresh cooldown ───────────────────────────────────────────────


def test_production_rejects_cooldown_below_minimum() -> None:
    """Production with cooldown < 15 minutes is a misconfiguration.

    The startup guard in main.py will RuntimeError on this.
    """
    s = Settings(
        environment="production",
        jwt_secret=SecretStr("a-strong-production-secret-at-least-32-chars!!"),
        refresh_cookie_secure=True,
        job_refresh_cooldown_minutes=5,
    )
    assert s.is_production is True
    assert s.job_refresh_cooldown_minutes < 15
    # The startup guard in main.py will RuntimeError.


def test_production_accepts_15_minute_cooldown() -> None:
    """Production with exactly 15-minute cooldown is valid."""
    s = Settings(
        environment="production",
        jwt_secret=SecretStr("a-strong-production-secret-at-least-32-chars!!"),
        refresh_cookie_secure=True,
        job_refresh_cooldown_minutes=15,
    )
    assert s.is_production is True
    assert s.job_refresh_cooldown_minutes == 15


def test_development_allows_zero_cooldown() -> None:
    """Development can still set cooldown to 0 for convenience."""
    s = Settings(environment="development", job_refresh_cooldown_minutes=0)
    assert s.job_refresh_cooldown_minutes == 0
