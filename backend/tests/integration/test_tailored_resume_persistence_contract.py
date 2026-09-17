"""Regression coverage for real source-backed tailored-draft persistence."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.jobs.dedup import job_fingerprint
from app.domain.jobs.identity import scoped_key
from app.domain.jobs.repository import JobRepository
from app.domain.resume.repository import ResumeRepository
from app.domain.resume.schemas import ParsedProfile, TailoredResumeDraftCreate
from app.domain.resume.service import ResumeService
from app.models.job import Job
from app.models.resume import CandidateProfile, Resume, SourceResumeContent, TailoredResumeDraft
from app.models.user import User
from tests.unit.resume_qa_support import (
    source_backed_wrap_content,
    source_backed_wrap_source_payload,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


@pytest.fixture(autouse=True)
async def clean_db(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await db.execute(text("TRUNCATE TABLE users CASCADE"))
        await db.commit()


@pytest.mark.asyncio
async def test_real_source_backed_snapshot_rehydrates_validates_and_persists(
    engine: AsyncEngine,
) -> None:
    """The concrete repositories must accept a realistic verified v5 snapshot."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    marker = uuid4().hex[:12]
    source_content, source_document = source_backed_wrap_source_payload()

    async with factory() as db:
        user = User(email=f"tailored-{marker}@example.test", name="Tailored fixture")
        job = Job(
            fingerprint=job_fingerprint("Acme", "Backend Engineer", "Remote"),
            identity_scope="public",
            identity_key=scoped_key("public", f"tailored-{marker}"),
            title="Backend Engineer",
            company="Acme",
            location="Remote",
            source="fixture",
            source_url=f"https://example.test/jobs/{marker}",
            visibility="public",
            ingest_source="scraper",
            status="active",
            description="Python backend platform engineer",
            raw_response={},
        )
        db.add_all([user, job])
        await db.flush()

        resume = Resume(user_id=user.id, filename="source.txt", raw_text="fixture source")
        db.add(resume)
        await db.flush()
        profile = CandidateProfile(
            user_id=user.id,
            resume_id=resume.id,
            parsed=ParsedProfile.model_validate({"skills": ["Python", "FastAPI"]}).model_dump(),
            summary_text="fixture source",
            embedding=[0.0] * get_settings().embedding_dim,
        )
        source = SourceResumeContent(
            resume_id=resume.id,
            schema_version="source-resume-v5",
            content=source_content,
            validation_status="verified",
            validation_errors=[],
        )
        db.add_all([profile, source])
        await db.flush()

        snapshot = source_backed_wrap_content(source_document, source)
        snapshot.job_id = job.id
        created = await ResumeService(ResumeRepository(db)).create_tailored_draft(
            user_id=user.id,
            payload=TailoredResumeDraftCreate(job_id=job.id, content=snapshot),
            job_repository=JobRepository(db),
        )
        await db.commit()

        saved = (
            await db.execute(
                select(TailoredResumeDraft).where(TailoredResumeDraft.id == created.id)
            )
        ).scalar_one()
        assert saved.source_profile_id == profile.id
        assert saved.source_resume_content_id == source.id
        assert saved.content["tailoring_snapshot_version"] == "full-context-v1"
        assert saved.content["source_revision_sha256"] == snapshot.source_revision_sha256
