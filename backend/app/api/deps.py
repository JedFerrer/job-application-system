"""Shared FastAPI dependencies.

``get_current_user`` is the single-user MVP shim: it returns the seeded user
without any token check. Swapping in real auth later is a one-line change here.
Every domain table already carries a ``user_id`` FK so nothing else changes.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.domain.applications.repository import ApplicationRepository
from app.domain.applications.service import ApplicationService
from app.domain.cover_letters.repository import CoverLetterRepository
from app.domain.cover_letters.service import CoverLetterService
from app.domain.jobs.repository import JobRepository
from app.domain.jobs.service import JobService
from app.domain.matching.repository import MatchingRepository
from app.domain.matching.service import MatchingService
from app.domain.resume.repository import ResumeRepository
from app.domain.resume.service import ResumeService
from app.infrastructure.database import get_db
from app.infrastructure.redis_client import get_redis
from app.infrastructure.task_store import TaskStore
from app.models.user import User

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(db: DbSession) -> User:
    """Return the seeded single user. Replace with token auth post-MVP."""
    settings = get_settings()
    result = await db.execute(
        select(User).where(User.email == settings.seed_user_email)
    )
    user = result.scalar_one_or_none()
    if user is None:
        # Seeded on startup; missing means a misconfigured deployment.
        raise HTTPException(status_code=500, detail="Seed user not provisioned")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_resume_service(db: DbSession) -> ResumeService:
    return ResumeService(ResumeRepository(db))


ResumeServiceDep = Annotated[ResumeService, Depends(get_resume_service)]


def get_job_service(db: DbSession) -> JobService:
    return JobService(JobRepository(db), user_timezone=get_settings().user_timezone)


JobServiceDep = Annotated[JobService, Depends(get_job_service)]


def get_match_service(db: DbSession) -> MatchingService:
    return MatchingService(MatchingRepository(db))


MatchServiceDep = Annotated[MatchingService, Depends(get_match_service)]


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
