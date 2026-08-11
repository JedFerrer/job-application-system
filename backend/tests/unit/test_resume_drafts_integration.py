"""Database-backed lifecycle coverage for saved tailored resume snapshots."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.resume.repository import ResumeRepository
from app.domain.resume.schemas import TailoredResume
from app.models.job import Job
from app.models.resume import CandidateProfile, Resume, TailoredResumeDraft
from app.models.user import User

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine():
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def clean_db(engine) -> None:  # noqa: ANN001
    async with async_sessionmaker(engine, class_=AsyncSession)() as db:
        await db.execute(
            text(
                "TRUNCATE TABLE tailored_resume_drafts, jobs, "
                "candidate_profiles, resumes, users "
                "RESTART IDENTITY CASCADE"
            )
        )
        await db.commit()


def _content(job_id: int) -> dict[str, object]:
    return {
        "job_id": job_id,
        "professional_summary": "Saved backend draft",
        "full_name": "Ada Lovelace",
        "email": "ada@example.com",
        "phone": None,
        "headline": "Backend Engineer",
        "work_history": [],
        "education": [],
        "certifications": [],
        "matched_skills": ["python"],
        "other_skills": [],
        "missing_keywords": [],
    }


async def _seed_user(db: AsyncSession, user_id: int) -> None:
    db.add(
        User(
            id=user_id,
            email=f"user{user_id}@example.com",
            name=f"User {user_id}",
            is_active=True,
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_snapshot_survives_unavailable_links_and_list_is_owned_and_ordered(
    session_factory,
) -> None:  # noqa: ANN001
    timestamp = datetime(2026, 7, 15, tzinfo=UTC)
    async with session_factory() as db:
        await _seed_user(db, 1)
        await _seed_user(db, 2)
        db.add_all(
            [
                TailoredResumeDraft(
                    user_id=1,
                    job_id=None,
                    source_profile_id=None,
                    job_title="Lower ID snapshot",
                    job_company="Acme",
                    content=_content(101),
                    created_at=timestamp,
                    updated_at=timestamp,
                ),
                TailoredResumeDraft(
                    user_id=1,
                    job_id=None,
                    source_profile_id=None,
                    job_title="Higher ID snapshot",
                    job_company="Beta",
                    content=_content(102),
                    created_at=timestamp,
                    updated_at=timestamp,
                ),
                TailoredResumeDraft(
                    user_id=2,
                    job_id=None,
                    source_profile_id=None,
                    job_title="Other user's snapshot",
                    job_company="Private",
                    content=_content(103),
                    created_at=timestamp,
                    updated_at=timestamp,
                ),
            ]
        )
        await db.flush()
        repository = ResumeRepository(db)

        listed = await repository.list_tailored_drafts(user_id=1)
        assert [draft.job_title for draft in listed] == [
            "Higher ID snapshot",
            "Lower ID snapshot",
        ]
        assert listed[0].content["job_id"] == 102
        assert await repository.get_tailored_draft(user_id=1, draft_id=listed[0].id)
        assert await repository.get_tailored_draft(user_id=2, draft_id=listed[0].id) is None

        snapshot = await repository.get_tailored_draft(user_id=1, draft_id=listed[0].id)
        assert snapshot is not None
        assert snapshot.job_id is None
        assert snapshot.source_profile_id is None
        assert snapshot.job_title == "Higher ID snapshot"
        assert snapshot.job_company == "Beta"
        assert snapshot.content == _content(102)


@pytest.mark.asyncio
async def test_snapshot_survives_real_linked_record_deletes(
    session_factory,
) -> None:  # noqa: ANN001
    async with session_factory() as db:
        await _seed_user(db, 1)
        resume = Resume(
            user_id=1,
            filename="source.pdf",
            raw_text="source resume",
            is_active=True,
        )
        db.add(resume)
        await db.flush()

        profile = CandidateProfile(
            user_id=1,
            resume_id=resume.id,
            parsed={"full_name": "Ada Lovelace", "skills": ["python"]},
            summary_text="Backend Engineer",
            embedding=[0.0] * 1536,
        )
        job = Job(
            fingerprint="phase10-fk-test",
            title="Backend Engineer",
            company="Acme",
            source="test",
            source_url="https://example.com/phase10-fk-test",
            raw_response={},
            description="Backend role",
        )
        db.add_all([profile, job])
        await db.flush()

        content_snapshot = _content(job.id)
        draft = TailoredResumeDraft(
            user_id=1,
            job_id=job.id,
            source_profile_id=profile.id,
            job_title=job.title,
            job_company=job.company,
            content=content_snapshot,
        )
        db.add(draft)
        await db.flush()
        draft_id = draft.id
        assert draft.job_id == job.id
        assert draft.source_profile_id == profile.id

        await db.delete(job)
        await db.delete(profile)
        await db.flush()
        await db.commit()

    async with session_factory() as db:
        draft = await db.scalar(
            select(TailoredResumeDraft).where(TailoredResumeDraft.id == draft_id)
        )
        assert draft is not None

        assert draft.job_id is None
        assert draft.source_profile_id is None
        assert draft.job_title == "Backend Engineer"
        assert draft.job_company == "Acme"
        assert draft.content == content_snapshot
        legacy_content = TailoredResume.model_validate(draft.content)
        assert legacy_content.requirement_classifications == []
        assert (
            legacy_content.model_dump(
                mode="json", exclude={"requirement_classifications"}
            )
            == content_snapshot
        )


@pytest.mark.asyncio
async def test_upsert_replaces_same_user_job_and_keeps_id(
    session_factory,
) -> None:  # noqa: ANN001
    async with session_factory() as db:
        await _seed_user(db, 1)
        job = Job(
            fingerprint="phase7-upsert-test",
            title="Backend Engineer",
            company="Acme",
            source="test",
            source_url="https://example.com/phase7-upsert-test",
            raw_response={},
            description="Backend role",
        )
        db.add(job)
        await db.flush()
        repository = ResumeRepository(db)

        first = await repository.create_tailored_draft(
            user_id=1,
            job_id=job.id,
            source_profile_id=None,
            job_title=job.title,
            job_company=job.company,
            content=_content(job.id) | {"professional_summary": "First"},
        )
        second = await repository.create_tailored_draft(
            user_id=1,
            job_id=job.id,
            source_profile_id=None,
            job_title=job.title,
            job_company=job.company,
            content=_content(job.id) | {"professional_summary": "Replacement"},
        )
        await db.commit()

        assert second.id == first.id
        assert second.source_profile_id is None
        assert second.content["professional_summary"] == "Replacement"
        rows = (
            await db.execute(
                select(TailoredResumeDraft).where(
                    TailoredResumeDraft.user_id == 1,
                    TailoredResumeDraft.job_id == job.id,
                )
            )
        ).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_upsert_is_independent_by_user_and_job(
    session_factory,
) -> None:  # noqa: ANN001
    async with session_factory() as db:
        await _seed_user(db, 1)
        await _seed_user(db, 2)
        jobs = [
            Job(
                fingerprint=f"phase7-scope-test-{index}",
                title=f"Role {index}",
                company="Acme",
                source="test",
                source_url=f"https://example.com/phase7-scope-test-{index}",
                raw_response={},
                description="Role",
            )
            for index in range(2)
        ]
        db.add_all(jobs)
        await db.flush()
        repository = ResumeRepository(db)

        rows = []
        for user_id, job in ((1, jobs[0]), (2, jobs[0]), (1, jobs[1])):
            rows.append(
                await repository.create_tailored_draft(
                    user_id=user_id,
                    job_id=job.id,
                    source_profile_id=None,
                    job_title=job.title,
                    job_company=job.company,
                    content=_content(job.id),
                )
            )
        await db.commit()

        assert len({row.id for row in rows}) == 3
        assert len(await repository.list_tailored_drafts(user_id=1)) == 2
        assert len(await repository.list_tailored_drafts(user_id=2)) == 1
