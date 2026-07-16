"""FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

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
    auth as ext_auth,
    autofill as ext_autofill,
    jobs as ext_jobs,
)
from app.core.config import Settings, get_settings
from app.core.lifespan import lifespan, register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import limiter

configure_logging()
settings = get_settings()
logger = get_logger("startup")


def validate_startup_settings(settings: Settings) -> None:
    """Validate production-safety settings at startup.

    Raises ``RuntimeError`` for misconfigured production settings.
    Logs warnings for detectable but non-fatal misconfigurations.
    """
    if settings.is_production and (
        settings.jwt_secret.get_secret_value() == "insecure-dev-secret-change-me"
    ):
        raise RuntimeError(
            "JWT_SECRET is using the insecure default in production. Set a strong secret."
        )
    elif settings.jwt_secret.get_secret_value() == "insecure-dev-secret-change-me":
        logger.warning("jwt_secret_default", detail="Using insecure default JWT secret")

    if settings.is_production and not settings.refresh_cookie_secure:
        raise RuntimeError(
            "REFRESH_COOKIE_SECURE must be True in production. "
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

    if settings.is_production and settings.job_refresh_cooldown_minutes < 15:
        raise RuntimeError(
            "JOB_REFRESH_COOLDOWN_MINUTES must be at least 15 in production. "
            f"Got {settings.job_refresh_cooldown_minutes}. "
            "On-demand scraping is expensive; a low cooldown enables abuse."
        )


validate_startup_settings(settings)

app = FastAPI(
    title="Job Application API",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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
    if settings.is_production:
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
