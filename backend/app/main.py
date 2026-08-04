"""FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.v1 import (
    applications,
    auth,
    cover_letters,
    health,
    interview,
    jobs,
    matches,
    preferences,
    readiness_preferences,
    resume,
)
from app.api.v1.extension import (
    applications as ext_applications,
)
from app.api.v1.extension import (
    auth as ext_auth,
)
from app.api.v1.extension import (
    autofill as ext_autofill,
)
from app.api.v1.extension import (
    jobs as ext_jobs,
)
from app.core.config import Settings, get_settings
from app.core.lifespan import lifespan, register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import limiter

configure_logging()
settings = get_settings()
logger = get_logger("startup")

_INSECURE_JWT_SECRET = "insecure-dev-secret-change-me"  # noqa: S105 - intentional sentinel
_MIN_PUBLIC_JWT_SECRET_LENGTH = 32


def _is_local_origin(hostname: str) -> bool:
    normalized = hostname.lower().rstrip(".")
    if normalized in {"localhost", "host.docker.internal"}:
        return True
    if normalized.endswith((".localhost", ".local", ".localdomain", ".test")):
        return True
    try:
        address = ip_address(normalized)
    except ValueError:
        return False
    return (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_unspecified
    )


def _validate_public_origins(origins: list[str]) -> None:
    if not origins:
        raise RuntimeError(
            "ALLOWED_ORIGINS must contain at least one HTTPS origin in staging and production."
        )

    for origin in origins:
        candidate = origin.strip()
        if not candidate or candidate != origin or "*" in candidate:
            raise RuntimeError(
                "ALLOWED_ORIGINS must contain explicit HTTPS origins and must not use a wildcard."
            )
        try:
            parsed = urlsplit(candidate)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError:
            raise RuntimeError(
                "ALLOWED_ORIGINS must contain only non-local HTTPS origins in "
                "staging and production."
            ) from None

        if hostname and ":" in hostname:
            canonical_host = f"[{hostname}]"
        else:
            canonical_host = hostname or ""
        canonical_origin = f"https://{canonical_host}"
        if port is not None:
            canonical_origin += f":{port}"

        if (
            parsed.scheme != "https"
            or not hostname
            or not hostname.isascii()
            or "%" in parsed.netloc
            or parsed.path != ""
            or parsed.query
            or parsed.fragment
            or _is_local_origin(hostname)
            or port == 443
            or canonical_origin != candidate
        ):
            raise RuntimeError(
                "ALLOWED_ORIGINS must contain only non-local HTTPS origins in "
                "staging and production."
            )


def validate_startup_settings(settings: Settings) -> None:
    """Validate production-safety settings at startup.

    Raises ``RuntimeError`` for misconfigured production settings.
    Logs warnings for detectable but non-fatal misconfigurations.
    """
    is_default_secret = settings.jwt_secret.get_secret_value() == _INSECURE_JWT_SECRET
    if settings.requires_public_security and is_default_secret:
        raise RuntimeError(
            "JWT_SECRET is using the insecure default in a public deployment. Set a strong secret."
        )
    elif is_default_secret:
        logger.warning("jwt_secret_default", detail="Using insecure default JWT secret")

    if (
        settings.requires_public_security
        and len(settings.jwt_secret.get_secret_value()) < _MIN_PUBLIC_JWT_SECRET_LENGTH
    ):
        raise RuntimeError(
            "JWT_SECRET must be at least 32 characters in staging and production."
        )

    if settings.requires_public_security and not settings.refresh_cookie_secure:
        raise RuntimeError(
            "REFRESH_COOKIE_SECURE must be True in staging and production. "
            "Refresh cookies carry long-lived credentials and must only be sent over HTTPS."
        )

    if settings.refresh_cookie_samesite == "none" and not settings.refresh_cookie_secure:
        logger.warning(
            "refresh_cookie_samesite_none_without_secure",
            detail=(
                "SameSite=None requires Secure=True, otherwise browsers will reject "
                "the cookie. Set REFRESH_COOKIE_SECURE=true."
            ),
        )

    if settings.requires_public_security:
        _validate_public_origins(settings.allowed_origins)

    if settings.requires_public_security and settings.job_refresh_cooldown_minutes < 15:
        raise RuntimeError(
            "JOB_REFRESH_COOLDOWN_MINUTES must be at least 15 in staging and production. "
            f"Got {settings.job_refresh_cooldown_minutes}. "
            "On-demand scraping is expensive; a low cooldown enables abuse."
        )

    if settings.requires_public_security and settings.email_delivery_mode != "resend":
        raise RuntimeError(
            "EMAIL_DELIVERY_MODE must be resend in staging and production; "
            "console and mock delivery are development-only."
        )


validate_startup_settings(settings)

app = FastAPI(
    title="Job Application API",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Attach OWASP-recommended security headers to every response."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if settings.requires_public_security:
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains; preload"
        )
    return response


register_exception_handlers(app)

app.include_router(health.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(resume.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(matches.router, prefix="/api/v1")
app.include_router(preferences.router, prefix="/api/v1")
app.include_router(readiness_preferences.router, prefix="/api/v1")
app.include_router(interview.router, prefix="/api/v1")
app.include_router(cover_letters.router, prefix="/api/v1")
app.include_router(applications.router, prefix="/api/v1")
app.include_router(ext_auth.router, prefix="/api/v1")
app.include_router(ext_jobs.router, prefix="/api/v1")
app.include_router(ext_applications.router, prefix="/api/v1")
app.include_router(ext_autofill.router, prefix="/api/v1")
app.include_router(ext_auth.web_router, prefix="/api/v1")
