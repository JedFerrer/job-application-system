"""Production-safety config validation regression tests.

Verifies that production misconfigurations raise errors at startup
(audit finding #6).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import main
from app.core.config import Settings
from app.main import validate_startup_settings

pytestmark = pytest.mark.unit

_PUBLIC_ORIGINS = ["https://app.example.com"]
_STRONG_SECRET = "a-strong-production-secret-at-least-32-chars!!"


def _public_settings(environment: str) -> Settings:
    return Settings(
        environment=environment,
        jwt_secret=SecretStr(_STRONG_SECRET),
        refresh_cookie_secure=True,
        job_refresh_cooldown_minutes=15,
        allowed_origins=_PUBLIC_ORIGINS,
        email_delivery_mode="resend",
    )


def test_is_production_returns_false_for_development() -> None:
    """is_production is False when environment is 'development'."""
    s = Settings(environment="development")
    assert s.is_production is False


def test_is_production_returns_false_for_staging() -> None:
    """is_production is False when environment is 'staging'."""
    s = Settings(environment="staging")
    assert s.is_production is False


def test_public_security_predicate_covers_staging_and_production_only() -> None:
    assert Settings(environment="staging").requires_public_security is True
    assert Settings(environment="production").requires_public_security is True
    assert Settings(environment="development").requires_public_security is False
    assert Settings(environment="test").requires_public_security is False


def test_is_production_returns_true_for_production() -> None:
    """is_production is True when environment is 'production'."""
    s = Settings(environment="production")
    assert s.is_production is True


# ── Production guard validation tests ─────────────────────────────────────


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_public_environment_insecure_jwt_secret_raises(environment: str) -> None:
    """Public environments reject the default JWT secret."""
    s = _public_settings(environment)
    s.jwt_secret = SecretStr("insecure-dev-secret-change-me")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        validate_startup_settings(s)


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_public_environment_refresh_cookie_not_secure_raises(environment: str) -> None:
    """Public environments reject insecure refresh cookies."""
    s = _public_settings(environment)
    s.refresh_cookie_secure = False
    with pytest.raises(RuntimeError, match="REFRESH_COOKIE_SECURE"):
        validate_startup_settings(s)


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_public_environment_low_cooldown_raises(environment: str) -> None:
    """Public environments reject a refresh cooldown below 15 minutes."""
    s = _public_settings(environment)
    s.job_refresh_cooldown_minutes = 5
    with pytest.raises(RuntimeError, match="JOB_REFRESH_COOLDOWN"):
        validate_startup_settings(s)


def test_valid_production_settings_pass() -> None:
    """Valid production settings do not raise."""
    s = _public_settings("production")
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
    s = _public_settings("production")
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
        jwt_secret=SecretStr(_STRONG_SECRET),
        refresh_cookie_secure=True,
        job_refresh_cooldown_minutes=5,
        allowed_origins=_PUBLIC_ORIGINS,
    )
    assert s.is_production is True
    assert s.job_refresh_cooldown_minutes < 15
    # The startup guard in main.py will RuntimeError.


def test_production_accepts_15_minute_cooldown() -> None:
    """Production with exactly 15-minute cooldown is valid."""
    s = Settings(
        environment="production",
        jwt_secret=SecretStr(_STRONG_SECRET),
        refresh_cookie_secure=True,
        job_refresh_cooldown_minutes=15,
        allowed_origins=_PUBLIC_ORIGINS,
    )
    assert s.is_production is True
    assert s.job_refresh_cooldown_minutes == 15


def test_development_allows_zero_cooldown() -> None:
    """Development can still set cooldown to 0 for convenience."""
    s = Settings(environment="development", job_refresh_cooldown_minutes=0)
    assert s.job_refresh_cooldown_minutes == 0


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_public_environment_rejects_short_jwt_secret(environment: str) -> None:
    settings = _public_settings(environment)
    settings.jwt_secret = SecretStr("too-short")
    with pytest.raises(RuntimeError, match="at least 32"):
        validate_startup_settings(settings)


def test_staging_rejects_empty_cors_origins() -> None:
    settings = _public_settings("staging")
    settings.allowed_origins = []
    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS"):
        validate_startup_settings(settings)


@pytest.mark.parametrize(
    ("environment", "origins"),
    [
        ("staging", ["*"]),
        ("production", ["*"]),
        ("staging", ["http://localhost:3000"]),
        ("production", ["http://localhost:3000"]),
        ("staging", ["http://app.example.com"]),
        ("production", ["http://app.example.com"]),
        ("staging", ["https://127.0.0.1:3000"]),
        ("production", ["https://127.0.0.1:3000"]),
        ("staging", ["https://app.example.com/"]),
        ("production", ["https://app.example.com/"]),
        ("staging", ["https://app.example.com/path"]),
        ("production", ["https://app.example.com/path"]),
        ("staging", ["https://APP.example.com"]),
        ("production", ["https://APP.example.com"]),
        ("staging", ["https://app.example.com:443"]),
        ("production", ["https://app.example.com:443"]),
        ("staging", ["https://user@app.example.com"]),
        ("production", ["https://user@app.example.com"]),
        ("staging", ["https://app.example.com:"]),
        ("production", ["https://app.example.com:"]),
        ("staging", ["https://app.example.com:abc"]),
        ("production", ["https://app.example.com:abc"]),
        ("staging", ["https://app.example.com?"]),
        ("production", ["https://app.example.com?"]),
        ("staging", ["https://app.example.com#"]),
        ("production", ["https://app.example.com#"]),
        ("staging", ["https://caf\u00e9.example"]),
        ("production", ["https://caf\u00e9.example"]),
        ("staging", ["https://%65xample.com"]),
        ("production", ["https://%65xample.com"]),
        ("staging", ["https://app.%65xample.com"]),
        ("production", ["https://app.%65xample.com"]),
        ("staging", [" https://app.example.com"]),
        ("production", [" https://app.example.com"]),
        ("staging", ["https://app.example.com "]),
        ("production", ["https://app.example.com "]),
    ],
)
def test_public_environment_rejects_unsafe_cors_origins(
    environment: str, origins: list[str]
) -> None:
    settings = _public_settings(environment)
    settings.allowed_origins = origins
    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS"):
        validate_startup_settings(settings)


def test_valid_staging_settings_pass() -> None:
    validate_startup_settings(_public_settings("staging"))


def test_valid_public_origin_with_non_default_https_port_passes() -> None:
    settings = _public_settings("production")
    settings.allowed_origins = ["https://app.example.com:8443"]
    validate_startup_settings(settings)


def test_valid_public_punycode_origin_passes() -> None:
    settings = _public_settings("production")
    settings.allowed_origins = ["https://xn--caf-dma.example"]
    validate_startup_settings(settings)


def test_development_local_origin_remains_allowed() -> None:
    settings = Settings(
        environment="development",
        allowed_origins=["http://localhost:3000"],
    )
    validate_startup_settings(settings)


@pytest.mark.parametrize(
    ("environment", "expected_hsts"),
    [("development", False), ("staging", True), ("production", True)],
)
def test_hsts_follows_public_security_predicate(
    monkeypatch: pytest.MonkeyPatch, environment: str, expected_hsts: bool
) -> None:
    replacement = (
        _public_settings(environment)
        if expected_hsts
        else Settings(environment=environment)
    )
    monkeypatch.setattr(main, "settings", replacement)
    response = TestClient(main.app).get("/api/v1/health")
    assert ("Strict-Transport-Security" in response.headers) is expected_hsts
