"""Application lifespan and global exception handlers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.applications.exceptions import ApplicationNotFoundError
from app.infrastructure.redis_client import close_redis, init_redis
from app.domain.cover_letters.exceptions import (
    CoverLetterGenerationError,
    CoverLetterNotFoundError,
)
from app.domain.jobs.exceptions import JobNotFoundError
from app.domain.matching.exceptions import (
    MatchNotFoundError,
    NoActiveProfileError,
)
from app.domain.resume.exceptions import (
    ProfileNotFoundError,
    ResumeParseError,
    UnsupportedResumeFormatError,
)
from app.infrastructure.database import async_session_factory
from app.models.user import User

logger = get_logger("lifespan")


async def _seed_user() -> None:
    """Ensure the single MVP user exists (idempotent)."""
    settings = get_settings()
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email == settings.seed_user_email)
        )
        if result.scalar_one_or_none() is None:
            session.add(User(email=settings.seed_user_email, name=settings.seed_user_name))
            await session.commit()
            logger.info("seed_user_created", email=settings.seed_user_email)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    await init_redis(settings.redis_url)
    await _seed_user()
    yield
    await close_redis()


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProfileNotFoundError)
    async def _profile_not_found(
        request: Request, exc: ProfileNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "No resume profile found"})

    @app.exception_handler(UnsupportedResumeFormatError)
    async def _unsupported_format(
        request: Request, exc: UnsupportedResumeFormatError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": exc.detail})

    @app.exception_handler(ResumeParseError)
    async def _parse_error(
        request: Request, exc: ResumeParseError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=502, content={"detail": "Failed to parse resume with AI"}
        )

    @app.exception_handler(JobNotFoundError)
    async def _job_not_found(
        request: Request, exc: JobNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404, content={"detail": f"Job {exc.job_id} not found"}
        )

    @app.exception_handler(NoActiveProfileError)
    async def _no_active_profile(
        request: Request, exc: NoActiveProfileError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": "No active resume profile to match against"},
        )

    @app.exception_handler(MatchNotFoundError)
    async def _match_not_found(
        request: Request, exc: MatchNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": f"No match found for job {exc.job_id}"},
        )

    @app.exception_handler(CoverLetterNotFoundError)
    async def _cover_letter_not_found(
        request: Request, exc: CoverLetterNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": f"Cover letter {exc.cover_letter_id} not found"},
        )

    @app.exception_handler(CoverLetterGenerationError)
    async def _cover_letter_generation_failed(
        request: Request, exc: CoverLetterGenerationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"detail": "Failed to generate cover letter"},
        )

    @app.exception_handler(ApplicationNotFoundError)
    async def _application_not_found(
        request: Request, exc: ApplicationNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": f"Application {exc.application_id} not found"},
        )
