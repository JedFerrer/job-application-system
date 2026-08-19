"""Database-backed lifecycle coverage for saved tailored resume snapshots."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.resume.repository import ResumeRepository
from app.domain.resume.source_content import (
    build_source_candidate_ledger,
    resolve_source_layout_v3,
)
from app.models.job import Job
from app.models.resume import CandidateProfile, Resume, SourceResumeContent, TailoredResumeDraft
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


async def _seed_current_provenance(
    db: AsyncSession, *, user_id: int, suffix: str
) -> tuple[CandidateProfile, SourceResumeContent]:
    """Create the smallest persisted source/profile graph valid for TFR-9 drafts."""
    resume = Resume(
        user_id=user_id,
        filename=f"source-{suffix}.pdf",
        raw_text="Source summary",
        is_active=True,
    )
    db.add(resume)
    await db.flush()
    ledger = build_source_candidate_ledger("Source summary")
    layout = resolve_source_layout_v3({"summary": "c0"}, ledger)
    assert layout.status == "verified"
    assert layout.layout is not None
    source = SourceResumeContent(
        resume_id=resume.id,
        schema_version="source-resume-v3",
        content={
            "schema_version": "source-resume-v3",
            "document": {
                "candidates": [item.model_dump(mode="json") for item in ledger.candidates],
                "layout": layout.layout.model_dump(mode="json"),
                "artifacts": [],
            },
            "layout_validation_status": "verified",
            "layout_validation_errors": [],
        },
        validation_status="verified",
        validation_errors=[],
    )
    profile = CandidateProfile(
        user_id=user_id,
        resume_id=resume.id,
        parsed={"full_name": "Ada Lovelace", "skills": ["python"]},
        summary_text="Backend Engineer",
        embedding=[0.0] * 1536,
    )
    db.add_all([source, profile])
    await db.flush()
    return profile, source


@pytest.mark.asyncio
async def test_source_backed_snapshot_is_owned_and_ordered(
    session_factory,
) -> None:  # noqa: ANN001
    timestamp = datetime(2026, 7, 15, tzinfo=UTC)
    async with session_factory() as db:
        await _seed_user(db, 1)
        await _seed_user(db, 2)
        profile_1a, source_1a = await _seed_current_provenance(db, user_id=1, suffix="1a")
        profile_1b, source_1b = await _seed_current_provenance(db, user_id=1, suffix="1b")
        profile_2, source_2 = await _seed_current_provenance(db, user_id=2, suffix="2")
        db.add_all(
            [
                TailoredResumeDraft(
                    user_id=1,
                    job_id=None,
                    source_profile_id=profile_1a.id,
                    source_resume_content_id=source_1a.id,
                    job_title="Lower ID snapshot",
                    job_company="Acme",
                    content=_content(101),
                    created_at=timestamp,
                    updated_at=timestamp,
                ),
                TailoredResumeDraft(
                    user_id=1,
                    job_id=None,
                    source_profile_id=profile_1b.id,
                    source_resume_content_id=source_1b.id,
                    job_title="Higher ID snapshot",
                    job_company="Beta",
                    content=_content(102),
                    created_at=timestamp,
                    updated_at=timestamp,
                ),
                TailoredResumeDraft(
                    user_id=2,
                    job_id=None,
                    source_profile_id=profile_2.id,
                    source_resume_content_id=source_2.id,
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
        assert snapshot.source_profile_id == profile_1b.id
        assert snapshot.source_resume_content_id == source_1b.id
        assert snapshot.job_title == "Higher ID snapshot"
        assert snapshot.job_company == "Beta"
        assert snapshot.content == _content(102)


@pytest.mark.asyncio
async def test_source_or_profile_deletion_cascades_only_its_tied_draft(
    session_factory,
) -> None:  # noqa: ANN001
    async with session_factory() as db:
        await _seed_user(db, 1)
        profile_a, source_a = await _seed_current_provenance(db, user_id=1, suffix="cascade-a")
        profile_b, source_b = await _seed_current_provenance(db, user_id=1, suffix="cascade-b")
        job_a = Job(
            fingerprint="tfr9-cascade-a",
            title="Backend Engineer",
            company="Acme",
            source="test",
            source_url="https://example.com/tfr9-cascade-a",
            raw_response={},
            description="Backend role",
        )
        job_b = Job(
            fingerprint="tfr9-cascade-b",
            title="Platform Engineer",
            company="Beta",
            source="test",
            source_url="https://example.com/tfr9-cascade-b",
            raw_response={},
            description="Platform role",
        )
        db.add_all([job_a, job_b])
        await db.flush()

        draft_a = TailoredResumeDraft(
            user_id=1,
            job_id=job_a.id,
            source_profile_id=profile_a.id,
            source_resume_content_id=source_a.id,
            job_title=job_a.title,
            job_company=job_a.company,
            content=_content(job_a.id),
        )
        draft_b = TailoredResumeDraft(
            user_id=1,
            job_id=job_b.id,
            source_profile_id=profile_b.id,
            source_resume_content_id=source_b.id,
            job_title=job_b.title,
            job_company=job_b.company,
            content=_content(job_b.id),
        )
        db.add_all([draft_a, draft_b])
        await db.flush()
        draft_a_id, draft_b_id = draft_a.id, draft_b.id

        await db.delete(source_a)
        await db.flush()
        await db.commit()

    async with session_factory() as db:
        assert (
            await db.scalar(
                select(TailoredResumeDraft).where(TailoredResumeDraft.id == draft_a_id)
            )
            is None
        )
        assert (
            await db.scalar(
                select(TailoredResumeDraft).where(TailoredResumeDraft.id == draft_b_id)
            )
            is not None
        )
        profile_b_id = profile_b.id
        profile_b = await db.scalar(
            select(CandidateProfile).where(CandidateProfile.id == profile_b_id)
        )
        assert profile_b is not None
        await db.delete(profile_b)
        await db.commit()

    async with session_factory() as db:
        assert (
            await db.scalar(
                select(TailoredResumeDraft).where(TailoredResumeDraft.id == draft_b_id)
            )
            is None
        )


@pytest.mark.asyncio
async def test_upsert_replaces_same_user_job_and_keeps_id(
    session_factory,
) -> None:  # noqa: ANN001
    async with session_factory() as db:
        await _seed_user(db, 1)
        profile, source = await _seed_current_provenance(db, user_id=1, suffix="upsert")
        job = Job(
            fingerprint="tfr9-upsert-01",
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
            source_profile_id=profile.id,
            source_resume_content_id=source.id,
            job_title=job.title,
            job_company=job.company,
            content=_content(job.id) | {"professional_summary": "First"},
        )
        second = await repository.create_tailored_draft(
            user_id=1,
            job_id=job.id,
            source_profile_id=profile.id,
            source_resume_content_id=source.id,
            job_title=job.title,
            job_company=job.company,
            content=_content(job.id) | {"professional_summary": "Replacement"},
        )
        await db.commit()

        assert second.id == first.id
        assert second.source_profile_id == profile.id
        assert second.source_resume_content_id == source.id
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
        profile_1, source_1 = await _seed_current_provenance(db, user_id=1, suffix="scope-1")
        profile_2, source_2 = await _seed_current_provenance(db, user_id=2, suffix="scope-2")
        jobs = [
            Job(
                fingerprint=f"tfr9-scope-{index}",
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
            profile, source = (profile_1, source_1) if user_id == 1 else (profile_2, source_2)
            rows.append(
                await repository.create_tailored_draft(
                    user_id=user_id,
                    job_id=job.id,
                    source_profile_id=profile.id,
                    source_resume_content_id=source.id,
                    job_title=job.title,
                    job_company=job.company,
                    content=_content(job.id),
                )
            )
        await db.commit()

        assert len({row.id for row in rows}) == 3
        assert len(await repository.list_tailored_drafts(user_id=1)) == 2
        assert len(await repository.list_tailored_drafts(user_id=2)) == 1
