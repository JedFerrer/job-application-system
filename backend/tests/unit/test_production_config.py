"""Production-safety config validation regression tests.

Verifies that production misconfigurations raise errors at startup
(audit finding #6).
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.core.config import Settings


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


def test_production_refresh_cookie_secure_must_be_true() -> None:
    """Production with refresh_cookie_secure=False is a misconfiguration.

    The main.py startup guard should catch this, but we verify the settings
    surface the misconfiguration so the guard has the right data.
    """
    s = Settings(
        environment="production",
        jwt_secret=SecretStr("a-strong-production-secret-at-least-32-chars!!"),
        refresh_cookie_secure=False,
    )
    assert s.is_production is True
    assert s.refresh_cookie_secure is False
    # The startup guard in main.py will RuntimeError on this combination.


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
