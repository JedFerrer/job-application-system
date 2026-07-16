"""Shared FastAPI dependencies.

``get_current_user`` authenticates the request from a JWT access token sent as
``Authorization: Bearer <token>``. Every domain table carries a ``user_id`` FK,
so the resolved user scopes all queries to that account.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import TokenError, decode_token, decode_token_payload
from app.domain.applications.repository import ApplicationRepository
from app.domain.applications.service import ApplicationService
from app.domain.auth.exceptions import InvalidTokenError
from app.domain.auth.repository import UserRepository
from app.domain.auth.service import AuthService
from app.domain.auth.web_session_repository import WebSessionRepository
from app.domain.cover_letters.repository import CoverLetterRepository
from app.domain.cover_letters.service import CoverLetterService
from app.domain.extension.repository import ExtensionRepository
from app.domain.interview.repository import InterviewRepository
from app.domain.interview.service import InterviewService
from app.domain.jobs.repository import JobRepository
from app.domain.jobs.service import JobService
from app.domain.matching.repository import MatchingRepository
from app.domain.matching.readiness_preferences import (
    ReadinessPreferenceRepository,
    ReadinessPreferenceService,
)
from app.domain.matching.service import MatchingService
from app.domain.preferences.repository import PreferenceRepository
from app.domain.preferences.service import PreferenceService
from app.domain.resume.repository import ResumeRepository
from app.domain.resume.service import ResumeService
from app.infrastructure.database import get_db
from app.infrastructure.redis_client import get_redis
from app.infrastructure.task_store import TaskStore
from app.models.user import User

DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_app_settings() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def get_auth_service(db: DbSession) -> AuthService:
    return AuthService(
        UserRepository(db),
        get_settings(),
        web_sessions=WebSessionRepository(db),
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def _extract_bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise InvalidTokenError
    return token.strip()


async def get_current_user(request: Request, db: DbSession) -> User:
    """Resolve the authenticated user from the access-token Bearer header."""
    token = _extract_bearer_token(request)
    settings = get_settings()
    try:
        user_id = decode_token(
            settings=settings, token=token, expected_type="access"
        )
    except TokenError as exc:
        raise InvalidTokenError from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise InvalidTokenError
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_extension_user(request: Request, db: DbSession) -> User:
    """Resolve the authenticated user from an extension access token.

    Like ``get_current_user`` but also verifies that the token carries a
    valid ``session_id`` claim linking it to an active (non-revoked)
    extension session.  A revoked extension session cannot use its old
    access tokens — even if they haven't expired yet.
    """
    token = _extract_bearer_token(request)
    settings = get_settings()
    try:
        payload = decode_token_payload(
            settings=settings, token=token, expected_type="access"
        )
    except TokenError as exc:
        raise InvalidTokenError from exc

    user_id = int(payload["sub"])
    session_id = payload.get("session_id")
    if not session_id:
        raise InvalidTokenError

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise InvalidTokenError

    # Verify the extension session is still active.
    ext_repo = ExtensionRepository(db)
    session = await ext_repo.get_session(session_id)
    if session is None or session.user_id != user_id or session.revoked_at is not None:
        raise InvalidTokenError

    return user


CurrentExtensionUser = Annotated[User, Depends(get_current_extension_user)]


def get_resume_service(db: DbSession) -> ResumeService:
    return ResumeService(ResumeRepository(db))


ResumeServiceDep = Annotated[ResumeService, Depends(get_resume_service)]


def get_job_service(db: DbSession) -> JobService:
    return JobService(JobRepository(db), user_timezone=get_settings().user_timezone)


JobServiceDep = Annotated[JobService, Depends(get_job_service)]


def get_job_repository(db: DbSession) -> JobRepository:
    return JobRepository(db)


JobRepositoryDep = Annotated[JobRepository, Depends(get_job_repository)]


def get_match_service(db: DbSession) -> MatchingService:
    return MatchingService(MatchingRepository(db), ReadinessPreferenceRepository(db))


MatchServiceDep = Annotated[MatchingService, Depends(get_match_service)]


def get_readiness_preference_service(db: DbSession) -> ReadinessPreferenceService:
    return ReadinessPreferenceService(ReadinessPreferenceRepository(db))


ReadinessPreferenceServiceDep = Annotated[
    ReadinessPreferenceService, Depends(get_readiness_preference_service)
]


def get_preference_service(db: DbSession) -> PreferenceService:
    settings = get_settings()
    from app.domain.scraping.repository import ScrapeQueryRepository, SubscriptionRepository

    return PreferenceService(
        PreferenceRepository(db),
        allowed_sources=settings.enabled_job_sources,
        refresh_cooldown_minutes=settings.job_refresh_cooldown_minutes,
        default_results=settings.job_results_wanted,
        query_repository=ScrapeQueryRepository(db),
        subscription_repository=SubscriptionRepository(db),
    )


PreferenceServiceDep = Annotated[
    PreferenceService, Depends(get_preference_service)
]


def get_interview_service(db: DbSession) -> InterviewService:
    return InterviewService(InterviewRepository(db))


InterviewServiceDep = Annotated[InterviewService, Depends(get_interview_service)]


def get_cover_letter_service(db: DbSession) -> CoverLetterService:
    return CoverLetterService(
        repository=CoverLetterRepository(db),
        resume_repository=ResumeRepository(db),
        job_repository=JobRepository(db),
    )


CoverLetterServiceDep = Annotated[
    CoverLetterService, Depends(get_cover_letter_service)
]


def get_application_service(db: DbSession) -> ApplicationService:
    return ApplicationService(
        repository=ApplicationRepository(db),
        job_repository=JobRepository(db),
    )


ApplicationServiceDep = Annotated[
    ApplicationService, Depends(get_application_service)
]


def get_task_store() -> TaskStore:
    return TaskStore(get_redis())


TaskStoreDep = Annotated[TaskStore, Depends(get_task_store)]


# ── Extension Services (Phase 5 — Browser Extension) ──────────────────────

from app.domain.extension.extension_auth_service import ExtensionAuthService
from app.domain.extension.extension_job_import_service import ExtensionJobImportService
from app.domain.extension.application_event_service import ApplicationEventService
from app.domain.extension.autofill_mapping_service import AutofillProfileService


def get_extension_auth_service(db: DbSession) -> ExtensionAuthService:
    return ExtensionAuthService(
        repository=ExtensionRepository(db),
        settings=get_settings(),
        redis=get_redis(),
    )


ExtensionAuthServiceDep = Annotated[
    ExtensionAuthService, Depends(get_extension_auth_service)
]


def get_extension_job_import_service(db: DbSession) -> ExtensionJobImportService:
    return ExtensionJobImportService(
        job_repository=JobRepository(db),
        extension_repository=ExtensionRepository(db),
    )


ExtensionJobImportServiceDep = Annotated[
    ExtensionJobImportService, Depends(get_extension_job_import_service)
]


def get_application_event_service(db: DbSession) -> ApplicationEventService:
    return ApplicationEventService(
        extension_repository=ExtensionRepository(db),
        application_repository=ApplicationRepository(db),
        job_repository=JobRepository(db),
    )


ApplicationEventServiceDep = Annotated[
    ApplicationEventService, Depends(get_application_event_service)
]


def get_autofill_profile_service(db: DbSession) -> AutofillProfileService:
    return AutofillProfileService(
        resume_repository=ResumeRepository(db),
        extension_repository=ExtensionRepository(db),
    )


AutofillProfileServiceDep = Annotated[
    AutofillProfileService, Depends(get_autofill_profile_service)
]
