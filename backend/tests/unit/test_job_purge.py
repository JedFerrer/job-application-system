"""Integration coverage for expired-job retention and protected purge rules."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.jobs.identity import scoped_key
from app.domain.jobs.repository import ExpiredJobPurgeBatch, JobRepository
from app.models.application import Application
from app.models.application_event import ApplicationEvent
from app.models.application_form_session import ApplicationFormSession
from app.models.cover_letter import CoverLetter
from app.models.interview_prep import InterviewPrep
from app.models.job import Job
from app.models.job_source_link import JobSourceLink
from app.models.resume import CandidateProfile, Resume, SourceResumeContent, TailoredResumeDraft
from app.models.user import User
from app.models.user_job import UserJob
from app.models.user_job_import import UserJobImport
from app.models.user_job_match import UserJobMatch

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, poolclass=NullPool, echo=False)


@pytest.fixture(autouse=True)
async def clean_db(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await db.execute(text("TRUNCATE TABLE jobs, users RESTART IDENTITY CASCADE"))
        db.add(User(id=1, email="purge@test.com", name="Purge User", is_active=True))
        await db.commit()


async def _job(
    db: AsyncSession,
    *,
    job_id: int,
    status: str = "expired",
    expired_at: datetime | None = None,
    posted_at: datetime | None = None,
) -> Job:
    job = Job(
        id=job_id,
        fingerprint=f"purge{job_id:011d}",
        identity_scope="public",
        identity_key=scoped_key("public", f"legacy:purge:{job_id}"),
        title=f"Purge Job {job_id}",
        company="Acme",
        location="Remote",
        source="test",
        source_url=f"https://example.test/jobs/{job_id}",
        raw_response={"raw": "payload"},
        status=status,
        visibility="public",
        ingest_source="scraper",
        expired_at=expired_at,
        posted_at=posted_at,
    )
    db.add(job)
    await db.flush()
    return job


async def _purge(db: AsyncSession) -> ExpiredJobPurgeBatch:
    batch = await JobRepository(db).purge_expired_unreferenced(
        cutoff=datetime.now(UTC) - timedelta(days=90),
        batch_size=500,
    )
    await db.commit()
    return batch


async def test_purge_deletes_old_unreferenced_job_and_automatic_match(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        job = await _job(db, job_id=1, expired_at=datetime.now(UTC) - timedelta(days=91))
        db.add(UserJobMatch(user_id=1, job_id=job.id, vector_score=42, origin="automatic"))
        db.add(
            JobSourceLink(
                job_id=job.id,
                source="test",
                scope_key="public",
                identity_key=scoped_key("public", "legacy:purge:source-1"),
                source_job_id="purge-source-1",
                raw_payload={"source": "payload"},
            )
        )
        await db.commit()

        assert (await _purge(db)).totals() == {"scanned": 1, "protected": 0, "purged": 1}
        assert await db.get(Job, job.id) is None
        assert (await db.execute(select(UserJobMatch))).scalars().all() == []
        assert (await db.execute(select(JobSourceLink))).scalars().all() == []
        assert (await _purge(db)).totals() == {"scanned": 0, "protected": 0, "purged": 0}


async def test_purge_keeps_recent_active_and_timestampless_expired_jobs(
    engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        recent = await _job(db, job_id=2, expired_at=datetime.now(UTC) - timedelta(days=89))
        active = await _job(db, job_id=3, status="active")
        timestampless = await _job(db, job_id=4, expired_at=None)
        await db.commit()

        assert (await _purge(db)).totals() == {"scanned": 0, "protected": 0, "purged": 0}
        assert await db.get(Job, recent.id) is not None
        assert await db.get(Job, active.id) is not None
        assert await db.get(Job, timestampless.id) is not None


async def test_purge_preserves_every_kind_of_user_content(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        expired_at = datetime.now(UTC) - timedelta(days=91)
        jobs = [await _job(db, job_id=job_id, expired_at=expired_at) for job_id in range(10, 19)]
        by_id = {job.id: job for job in jobs}
        db.add(UserJob(user_id=1, job_id=10, relationship_source="match", status="archived"))
        db.add(Application(user_id=1, job_id=11, status="saved"))
        db.add(UserJobMatch(user_id=1, job_id=12, vector_score=80, origin="manual"))
        db.add(UserJob(user_id=1, job_id=13, relationship_source="manual", status="imported"))
        db.add(
            UserJobImport(
                user_id=1,
                user_job_user_id=1,
                user_job_job_id=13,
                job_id=13,
                import_method="manual",
            )
        )
        db.add(CoverLetter(user_id=1, job_id=14, content="Letter", model="test"))
        db.add(InterviewPrep(user_id=1, job_id=15, questions=[], focus_areas=[]))
        resume = Resume(
            user_id=1,
            filename="purge-source.pdf",
            raw_text="Source summary",
            is_active=True,
        )
        db.add(resume)
        await db.flush()
        source = SourceResumeContent(
            resume_id=resume.id,
            schema_version="source-resume-v3",
            content={
                "schema_version": "source-resume-v3",
                "document": {"candidates": [], "layout": None, "artifacts": []},
                "layout_validation_status": "verified",
                "layout_validation_errors": [],
            },
            validation_status="verified",
            validation_errors=[],
        )
        profile = CandidateProfile(
            user_id=1,
            resume_id=resume.id,
            parsed={"full_name": "Purge User", "skills": []},
            summary_text="Purge User",
            embedding=[0.0] * 1536,
        )
        db.add_all([source, profile])
        await db.flush()
        db.add(
            TailoredResumeDraft(
                user_id=1,
                job_id=16,
                source_profile_id=profile.id,
                source_resume_content_id=source.id,
                job_title="Purge Job 16",
                content={"summary": "tailored"},
            )
        )
        db.add(ApplicationEvent(id="event-17", user_id=1, job_id=17, event_type="detected"))
        db.add(ApplicationFormSession(id="session-18", user_id=1, job_id=18))
        await db.commit()

        assert (await _purge(db)).totals() == {"scanned": 9, "protected": 9, "purged": 0}
        for job in by_id.values():
            assert await db.get(Job, job.id) is not None


async def test_purge_cursor_advances_past_protected_batches(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        protected = await _job(
            db,
            job_id=20,
            expired_at=datetime.now(UTC) - timedelta(days=92),
        )
        eligible = await _job(
            db,
            job_id=21,
            expired_at=datetime.now(UTC) - timedelta(days=91),
        )
        db.add(UserJob(user_id=1, job_id=protected.id, relationship_source="match"))
        await db.commit()

        repository = JobRepository(db)
        first = await repository.purge_expired_unreferenced(
            cutoff=datetime.now(UTC) - timedelta(days=90),
            batch_size=1,
        )
        await db.commit()
        assert first.totals() == {"scanned": 1, "protected": 1, "purged": 0}
        assert first.next_cursor is not None

        second = await repository.purge_expired_unreferenced(
            cutoff=datetime.now(UTC) - timedelta(days=90),
            batch_size=1,
            after=first.next_cursor,
        )
        await db.commit()
        assert second.totals() == {"scanned": 1, "protected": 0, "purged": 1}
        assert await db.get(Job, protected.id) is not None
        assert await db.get(Job, eligible.id) is None


async def test_purge_candidate_lock_prevents_concurrent_event_detachment(
    engine: AsyncEngine,
) -> None:
    """A new event cannot be committed after purge has locked its job."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        job = await _job(db, job_id=22, expired_at=datetime.now(UTC) - timedelta(days=91))
        await db.commit()

    async with factory() as purge_db, factory() as event_db:
        await purge_db.execute(
            select(Job.id).where(Job.id == job.id).with_for_update(skip_locked=True)
        )

        async def create_event() -> None:
            event_db.add(
                ApplicationEvent(
                    id="event-concurrent",
                    user_id=1,
                    job_id=job.id,
                    event_type="detected",
                )
            )
            await event_db.commit()

        event_task = asyncio.create_task(create_event())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(event_task), timeout=0.1)

        await purge_db.execute(delete(Job).where(Job.id == job.id))
        await purge_db.commit()

        with pytest.raises(IntegrityError):
            await event_task
        await event_db.rollback()

    async with factory() as db:
        assert await db.get(Job, job.id) is None
        assert (
            await db.execute(
                select(ApplicationEvent).where(ApplicationEvent.id == "event-concurrent")
            )
        ).scalar_one_or_none() is None


async def test_archiving_sets_expired_at_once(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        job = await _job(
            db,
            job_id=30,
            status="active",
            posted_at=datetime.now(UTC) - timedelta(days=31),
        )
        repository = JobRepository(db)
        assert await repository.archive_older_than(datetime.now(UTC) - timedelta(days=30)) == 1
        await db.commit()
        await db.refresh(job)
        first_expired_at = job.expired_at

        assert first_expired_at is not None
        assert await repository.archive_older_than(datetime.now(UTC)) == 0
        await db.commit()
        await db.refresh(job)
        assert job.expired_at == first_expired_at
