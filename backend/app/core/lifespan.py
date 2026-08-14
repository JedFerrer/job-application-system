"""Application lifespan and global exception handlers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.rate_limit import RateLimitBackendUnavailableError, RateLimitExceededError
from app.domain.applications.exceptions import (
    ApplicationNotFoundError,
    InvalidApplicationStatusTransitionError,
)
from app.domain.auth.exceptions import (
    EmailAlreadyRegisteredError,
    GoogleAuthError,
    InactiveUserError,
    InvalidCredentialsError,
    InvalidTokenError,
)
from app.domain.cover_letters.exceptions import (
    CoverLetterGenerationError,
    CoverLetterNotFoundError,
)
from app.domain.extension.exceptions import (
    ExtensionSessionNotFoundError,
    ExtensionSessionRevokedError,
    PairingCodeExpiredError,
    PairingCodeInvalidError,
)
from app.domain.interview.exceptions import InterviewGenerationError
from app.domain.jobs.exceptions import JobNotFoundError
from app.domain.matching.exceptions import (
    MatchNotFoundError,
    MatchNotReadyError,
    MatchPreparingError,
    MatchUnavailableError,
    NoActiveProfileError,
)
from app.domain.preferences.exceptions import (
    PreferenceNotFoundError,
    PreferenceValidationError,
    RefreshCooldownError,
)
from app.domain.resume.exceptions import (
    ProfileNotFoundError,
    ResumeImportDraftNotFoundError,
    ResumeParseError,
    TailoredResumeDraftNotFoundError,
    TailoredResumeSnapshotInvalidError,
    UnsupportedResumeFormatError,
)
from app.infrastructure.redis_client import close_redis, init_redis

logger = get_logger("lifespan")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    await init_redis(settings.redis_url)
    yield
    await close_redis()


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RateLimitExceededError)
    async def _auth_rate_limited(
        request: Request, exc: RateLimitExceededError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many authentication attempts. Please try again later."},
            headers={
                "Retry-After": str(exc.retry_after_seconds),
                "Cache-Control": "no-store",
            },
        )

    @app.exception_handler(RateLimitBackendUnavailableError)
    async def _auth_rate_limiter_unavailable(
        request: Request, exc: RateLimitBackendUnavailableError
    ) -> JSONResponse:
        logger.warning(
            "auth_rate_limiter_unavailable_response",
            endpoint=request.url.path,
            failure_mode="closed",
        )
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Authentication is temporarily unavailable. Please try again later."
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(ProfileNotFoundError)
    async def _profile_not_found(
        request: Request, exc: ProfileNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "No resume profile found"})

    @app.exception_handler(ResumeImportDraftNotFoundError)
    async def _resume_import_draft_not_found(
        request: Request, exc: ResumeImportDraftNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Resume import not found"})

    @app.exception_handler(TailoredResumeDraftNotFoundError)
    async def _tailored_resume_draft_not_found(
        request: Request, exc: TailoredResumeDraftNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Draft not found"})

    @app.exception_handler(TailoredResumeSnapshotInvalidError)
    async def _tailored_resume_snapshot_invalid(
        request: Request, exc: TailoredResumeSnapshotInvalidError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": "The tailored resume is no longer valid; please regenerate it."},
        )

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

    @app.exception_handler(MatchNotReadyError)
    async def _match_not_ready(
        request: Request, exc: MatchNotReadyError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": "Job is still being prepared for matching"},
        )

    @app.exception_handler(MatchPreparingError)
    async def _match_preparing(
        request: Request, exc: MatchPreparingError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": "Preparing this job for matching. Please retry shortly."},
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(MatchUnavailableError)
    async def _match_unavailable(
        request: Request, exc: MatchUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": "This job has no description available for matching"},
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

    @app.exception_handler(InvalidApplicationStatusTransitionError)
    async def _invalid_application_status_transition(
        request: Request, exc: InvalidApplicationStatusTransitionError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(PreferenceNotFoundError)
    async def _preference_not_found(
        request: Request, exc: PreferenceNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404, content={"detail": "No job preferences saved"}
        )

    @app.exception_handler(PreferenceValidationError)
    async def _preference_invalid(
        request: Request, exc: PreferenceValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": exc.message})

    @app.exception_handler(RefreshCooldownError)
    async def _refresh_cooldown(
        request: Request, exc: RefreshCooldownError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Refresh is on cooldown",
                "retry_after_seconds": exc.retry_after_seconds,
            },
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )

    @app.exception_handler(InterviewGenerationError)
    async def _interview_generation_failed(
        request: Request, exc: InterviewGenerationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"detail": "Failed to generate interview prep"},
        )

    @app.exception_handler(InvalidTokenError)
    async def _invalid_token(
        request: Request, exc: InvalidTokenError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"detail": "Not authenticated"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(InvalidCredentialsError)
    async def _invalid_credentials(
        request: Request, exc: InvalidCredentialsError
    ) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(exc)})

    @app.exception_handler(InactiveUserError)
    async def _inactive_user(
        request: Request, exc: InactiveUserError
    ) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(EmailAlreadyRegisteredError)
    async def _email_taken(
        request: Request, exc: EmailAlreadyRegisteredError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(GoogleAuthError)
    async def _google_auth_failed(
        request: Request, exc: GoogleAuthError
    ) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": exc.detail})

    # ── Extension exception handlers ─────────────────────────────────────

    @app.exception_handler(PairingCodeInvalidError)
    async def _pairing_code_invalid(
        request: Request, exc: PairingCodeInvalidError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=401, content={"detail": "Invalid pairing code"}
        )

    @app.exception_handler(PairingCodeExpiredError)
    async def _pairing_code_expired(
        request: Request, exc: PairingCodeExpiredError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=410, content={"detail": "Pairing code has expired"}
        )

    @app.exception_handler(ExtensionSessionNotFoundError)
    async def _extension_session_not_found(
        request: Request, exc: ExtensionSessionNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404, content={"detail": "Extension session not found"}
        )

    @app.exception_handler(ExtensionSessionRevokedError)
    async def _extension_session_revoked(
        request: Request, exc: ExtensionSessionRevokedError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=401, content={"detail": "Extension session has been revoked"}
        )
