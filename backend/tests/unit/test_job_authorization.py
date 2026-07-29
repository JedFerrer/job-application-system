"""Authorization regression tests — scoped job lookups.

Verifies that :meth:`JobRepository.get_visible_by_id` and all downstream
service callers enforce the same visibility rules as ``list_jobs``:

- Public jobs are visible to all authenticated users.
- Private jobs are visible ONLY to their owner (``created_by_user_id``).
- Archived jobs are invisible even to the owner.
- Unauthorized access returns 404, never 403.

Requires a running PostgreSQL instance.
"""

from __future__ import annotations

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.applications.repository import ApplicationRepository
from app.domain.applications.service import ApplicationService
from app.domain.jobs.dedup import job_fingerprint
from app.domain.jobs.exceptions import JobNotFoundError
from app.domain.jobs.repository import JobRepository
from app.domain.jobs.service import JobService
from app.models.application import Application
from app.models.job import Job
from app.models.user import User
from app.models.user_job import UserJob

pytestmark = pytest.mark.integration

# ── DB fixtures (self-contained, same pattern as test_feed_separation.py) ─


@pytest.fixture(scope="module")
def engine():
    """Module-scoped async engine with NullPool."""
    settings = get_settings()
    return create_async_engine(settings.database_url, poolclass=NullPool, echo=False)


@pytest.fixture(autouse=True)
async def clean_db(engine) -> None:  # noqa: ANN001
    """Truncate all user-data tables before each test."""
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    tables = [
        "user_job_matches",
        "user_jobs",
        "user_job_imports",
        "application_form_sessions",
        "application_form_fields",
        "application_events",
        "applications",
        "cover_letters",
        "interview_preps",
        "candidate_profiles",
        "resumes",
        "extension_sessions",
        "user_job_preferences",
        "user_scrape_query_subscriptions",
        "scrape_queries",
        "job_source_links",
        "jobs",
        "users",
    ]
    async with session_factory() as db:
        for table in tables:
            await db.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        await db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────

_JOB_BASE = {
    "raw_response": {},
    "source_url": "https://example.com/default",
    "source": "test",
}


def _session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _create_user(db: AsyncSession, *, user_id: int, email: str, name: str) -> None:
    await db.execute(
        insert(User).values(
            id=user_id,
            email=email,
            name=name,
            is_active=True,
        )
    )
    await db.commit()


async def _create_job(
    db: AsyncSession,
    *,
    job_id: int,
    visibility: str = "public",
    created_by_user_id: int | None = None,
    status: str = "active",
    title: str = "Test Job",
    company: str = "Test Corp",
) -> Job:
    fp = job_fingerprint(company, title, f"location-{job_id}")
    await db.execute(
        insert(Job).values(
            id=job_id,
            fingerprint=fp,
            title=title,
            company=company,
            location="Remote",
            source="test",
            source_url=f"https://example.com/job/{job_id}",
            visibility=visibility,
            created_by_user_id=created_by_user_id,
            ingest_source="manual" if visibility == "private" else "scraper",
            status=status,
            raw_response={},
        )
    )
    await db.commit()
    result = await db.execute(select(Job).where(Job.id == job_id))
    return result.scalar_one()


# ── get_visible_by_id — repository-level tests ────────────────────────────


async def test_owner_can_access_private_job(engine) -> None:  # noqa: ANN001
    """The owner of a private job can retrieve it via get_visible_by_id."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="owner@test.com", name="Owner")
        await _create_job(
            db,
            job_id=100,
            visibility="private",
            created_by_user_id=1,
        )

    async with sf() as db:
        repo = JobRepository(db)
        job = await repo.get_visible_by_id(user_id=1, job_id=100)
        assert job is not None
        assert job.id == 100
        assert job.visibility == "private"
        assert job.created_by_user_id == 1


async def test_non_owner_cannot_access_private_job(engine) -> None:  # noqa: ANN001
    """A user who does not own a private job gets None from get_visible_by_id."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="owner@test.com", name="Owner")
        await _create_user(db, user_id=2, email="other@test.com", name="Other")
        await _create_job(
            db,
            job_id=101,
            visibility="private",
            created_by_user_id=1,
        )

    async with sf() as db:
        repo = JobRepository(db)
        job = await repo.get_visible_by_id(user_id=2, job_id=101)
        assert job is None


async def test_any_user_can_access_public_job(engine) -> None:  # noqa: ANN001
    """Any authenticated user can retrieve a public job via get_visible_by_id."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="a@test.com", name="User A")
        await _create_user(db, user_id=2, email="b@test.com", name="User B")
        await _create_job(
            db,
            job_id=102,
            visibility="public",
            created_by_user_id=None,
        )

    async with sf() as db:
        repo = JobRepository(db)
        job_a = await repo.get_visible_by_id(user_id=1, job_id=102)
        job_b = await repo.get_visible_by_id(user_id=2, job_id=102)
        assert job_a is not None
        assert job_b is not None
        assert job_a.id == 102
        assert job_b.id == 102


async def test_archived_job_not_visible(engine) -> None:  # noqa: ANN001
    """An archived job is invisible even to its owner via get_visible_by_id."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="owner@test.com", name="Owner")
        await _create_job(
            db,
            job_id=103,
            visibility="private",
            created_by_user_id=1,
        )
        # Link the job to the user, then archive it.
        repo = JobRepository(db)
        await repo.link_user_jobs(1, [103])
        await repo.set_user_job_status(1, 103, "archived")
        await db.commit()

    async with sf() as db:
        repo = JobRepository(db)
        job = await repo.get_visible_by_id(user_id=1, job_id=103)
        assert job is None


async def test_archiving_is_user_scoped_and_preserves_application(engine) -> None:  # noqa: ANN001
    """Archive creates a missing relationship without deleting applications."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=10, email="archive-a@test.com", name="A")
        await _create_user(db, user_id=11, email="archive-b@test.com", name="B")
        await _create_job(db, job_id=104)
        repo = JobRepository(db)
        await repo.link_user_jobs(11, [104])
        app = await ApplicationService(
            repository=ApplicationRepository(db),
            job_repository=repo,
        ).create(user_id=10, job_id=104)
        await db.commit()

    async with sf() as db:
        await JobService(JobRepository(db)).archive_job(user_id=10, job_id=104)
        await db.commit()

        archived = await db.execute(
            select(UserJob).where(UserJob.user_id == 10, UserJob.job_id == 104)
        )
        other_user = await db.execute(
            select(UserJob).where(UserJob.user_id == 11, UserJob.job_id == 104)
        )
        application = await db.execute(select(Application).where(Application.id == app.id))

        archived_user_job = archived.scalar_one()
        assert archived_user_job.status == "archived"
        assert archived_user_job.relationship_source == "match"
        assert other_user.scalar_one().status != "archived"
        assert application.scalar_one().status == "saved"


async def test_archive_is_idempotent_for_an_already_archived_job(engine) -> None:  # noqa: ANN001
    """Repeated archive requests leave one archived relationship row."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="repeat@test.com", name="Repeat")
        await _create_job(db, job_id=105)

        service = JobService(JobRepository(db))
        await service.archive_job(user_id=1, job_id=105)
        await service.archive_job(user_id=1, job_id=105)
        await db.commit()

        rows = await db.execute(select(UserJob).where(UserJob.user_id == 1, UserJob.job_id == 105))
        archived = rows.scalars().all()
        assert len(archived) == 1
        assert archived[0].status == "archived"


async def test_archive_preserves_existing_import_relationship_metadata(engine) -> None:  # noqa: ANN001
    """Archive changes only status when the user already imported the job."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="importer@test.com", name="Importer")
        await _create_job(
            db,
            job_id=106,
            visibility="private",
            created_by_user_id=1,
        )
        repo = JobRepository(db)
        await repo.link_user_jobs(
            1,
            [106],
            relationship_source="manual",
            import_method="manual",
            status="imported",
        )
        await JobService(repo).archive_job(user_id=1, job_id=106)
        await db.commit()

        row = await db.execute(select(UserJob).where(UserJob.user_id == 1, UserJob.job_id == 106))
        archived = row.scalar_one()
        assert archived.status == "archived"
        assert archived.relationship_source == "manual"
        assert archived.import_method == "manual"


@pytest.mark.parametrize(
    ("job_id", "status"),
    [(99999, None), (107, "expired")],
)
async def test_archive_rejects_nonexistent_or_inactive_jobs(
    engine, job_id: int, status: str | None
) -> None:  # noqa: ANN001
    """Archive never reports success or creates a row for an inaccessible job."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="missing@test.com", name="Missing")
        if status is not None:
            await _create_job(db, job_id=job_id, status=status)

        with pytest.raises(JobNotFoundError):
            await JobService(JobRepository(db)).archive_job(user_id=1, job_id=job_id)
        await db.commit()

        row = await db.execute(
            select(UserJob).where(UserJob.user_id == 1, UserJob.job_id == job_id)
        )
        assert row.scalar_one_or_none() is None


async def test_archive_rejects_another_users_private_job(engine) -> None:  # noqa: ANN001
    """Archive uses the same non-disclosing private-job visibility contract."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="owner-archive@test.com", name="Owner")
        await _create_user(db, user_id=2, email="other-archive@test.com", name="Other")
        await _create_job(
            db,
            job_id=108,
            visibility="private",
            created_by_user_id=1,
        )

        with pytest.raises(JobNotFoundError):
            await JobService(JobRepository(db)).archive_job(user_id=2, job_id=108)
        await db.commit()

        row = await db.execute(select(UserJob).where(UserJob.user_id == 2, UserJob.job_id == 108))
        assert row.scalar_one_or_none() is None


async def test_archived_list_and_restore_are_user_scoped_and_preserve_applications(engine) -> None:  # noqa: ANN001
    """Archived listings and restores operate only on the caller's relationship."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="restore-a@test.com", name="A")
        await _create_user(db, user_id=2, email="restore-b@test.com", name="B")
        await _create_job(db, job_id=109, title="Archived public")
        await _create_job(
            db, job_id=110, visibility="private", created_by_user_id=1, title="Archived private"
        )
        await _create_job(db, job_id=111, title="Active public")
        repo = JobRepository(db)
        await repo.link_user_jobs(1, [109, 110])
        app = await ApplicationService(
            repository=ApplicationRepository(db), job_repository=repo
        ).create(user_id=1, job_id=109)
        await repo.set_user_job_status(1, 109, "archived")
        await repo.set_user_job_status(1, 110, "archived")
        await db.commit()

    async with sf() as db:
        service = JobService(JobRepository(db))
        active = await service.list_jobs(user_id=1)
        archived = await service.list_jobs(user_id=1, archive_state="archived")
        other_archived = await service.list_jobs(user_id=2, archive_state="archived")
        assert {job.id for job in active.items} == {111}
        assert {job.id for job in archived.items} == {109, 110}
        assert other_archived.items == []

        await service.restore_job(user_id=1, job_id=109)
        await db.commit()

        restored = await db.execute(
            select(UserJob).where(UserJob.user_id == 1, UserJob.job_id == 109)
        )
        application = await db.execute(select(Application).where(Application.id == app.id))
        applications = await db.execute(select(Application).where(Application.job_id == 109))
        assert restored.scalar_one().status == "new"
        assert application.scalar_one().status == "saved"
        assert len(applications.scalars().all()) == 1
        assert {job.id for job in (await service.list_jobs(user_id=1)).items} == {109, 111}
        assert {
            job.id for job in (await service.list_jobs(user_id=1, archive_state="archived")).items
        } == {110}


@pytest.mark.parametrize("job_id", [99999, 112, 113, 114])
async def test_restore_rejects_non_archived_or_invisible_jobs(engine, job_id: int) -> None:  # noqa: ANN001
    """Restore uses the same non-disclosing contract as archive and detail."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="restore-owner@test.com", name="Owner")
        await _create_user(db, user_id=2, email="restore-other@test.com", name="Other")
        await _create_job(db, job_id=112, title="Not archived")
        await _create_job(
            db, job_id=113, visibility="private", created_by_user_id=2, title="Other private"
        )
        await _create_job(db, job_id=114, status="expired", title="Inactive archived")
        repo = JobRepository(db)
        await repo.link_user_jobs(1, [112, 114])
        await repo.link_user_jobs(2, [113])
        await repo.set_user_job_status(2, 113, "archived")
        await repo.set_user_job_status(1, 114, "archived")
        with pytest.raises(JobNotFoundError):
            await JobService(repo).restore_job(user_id=1, job_id=job_id)


async def test_restore_preserves_archived_import_metadata(engine) -> None:  # noqa: ANN001
    """Restoring an imported job changes only its per-user status."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="restore-import@test.com", name="Importer")
        await _create_job(
            db, job_id=115, visibility="private", created_by_user_id=1, title="Imported job"
        )
        repo = JobRepository(db)
        await repo.link_user_jobs(
            1,
            [115],
            relationship_source="manual",
            import_method="manual",
            status="archived",
        )
        await JobService(repo).restore_job(user_id=1, job_id=115)
        await db.commit()

        restored = await db.execute(
            select(UserJob).where(UserJob.user_id == 1, UserJob.job_id == 115)
        )
        relationship = restored.scalar_one()
        assert relationship.status == "new"
        assert relationship.relationship_source == "manual"
        assert relationship.import_method == "manual"


async def test_nonexistent_job_returns_none(engine) -> None:  # noqa: ANN001
    """get_visible_by_id returns None for a job ID that does not exist."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="u@test.com", name="User")
        repo = JobRepository(db)
        job = await repo.get_visible_by_id(user_id=1, job_id=99999)
        assert job is None


async def test_exact_company_count_and_filter_match_visible_public_jobs(engine) -> None:  # noqa: ANN001
    """Employer counts use the same exact-normalized public feed rule."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="u@test.com", name="User")
        await _create_user(db, user_id=2, email="other@test.com", name="Other")
        await _create_job(db, job_id=10, company="Acme   Corp", title="Platform Engineer")
        await _create_job(db, job_id=11, company=" acme corp ", title="Backend Engineer")
        await _create_job(db, job_id=12, company="Acme Corporation")
        await _create_job(db, job_id=13, company="Acme Corp", status="expired")
        await _create_job(
            db,
            job_id=14,
            company="Acme Corp",
            visibility="private",
            created_by_user_id=2,
        )

    async with sf() as db:
        repo = JobRepository(db)
        count = await repo.count_active_public_jobs_by_company(user_id=1, company=" ACME Corp ")
        rows, total, _ = await repo.list_jobs(user_id=1, company_exact="Acme Corp")

        assert count == 2
        assert total == 2
        assert {job.id for job in rows} == {10, 11}

        await repo.archive_user_job(user_id=1, job_id=10)
        await db.commit()
        assert await repo.count_active_public_jobs_by_company(user_id=1, company="Acme Corp") == 1


# ── JobService.get_job — service-level tests ──────────────────────────────


async def test_service_get_job_owner_sees_private(engine) -> None:  # noqa: ANN001
    """JobService.get_job returns the job for the owner."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="owner@test.com", name="Owner")
        await _create_job(
            db,
            job_id=200,
            visibility="private",
            created_by_user_id=1,
        )

    async with sf() as db:
        svc = JobService(JobRepository(db))
        result = await svc.get_job(user_id=1, job_id=200)
        assert result.id == 200


async def test_service_get_job_non_owner_gets_404(engine) -> None:  # noqa: ANN001
    """JobService.get_job raises JobNotFoundError for another user's private job."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="owner@test.com", name="Owner")
        await _create_user(db, user_id=2, email="other@test.com", name="Other")
        await _create_job(
            db,
            job_id=201,
            visibility="private",
            created_by_user_id=1,
        )

    async with sf() as db:
        svc = JobService(JobRepository(db))
        with pytest.raises(JobNotFoundError):
            await svc.get_job(user_id=2, job_id=201)


# ── ApplicationService.create — downstream service test ───────────────────


async def test_application_creation_rejects_other_user_private_job(engine) -> None:  # noqa: ANN001
    """ApplicationService.create raises JobNotFoundError for another user's private job."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="owner@test.com", name="Owner")
        await _create_user(db, user_id=2, email="other@test.com", name="Other")
        await _create_job(
            db,
            job_id=300,
            visibility="private",
            created_by_user_id=1,
        )

    async with sf() as db:
        svc = ApplicationService(
            repository=ApplicationRepository(db),
            job_repository=JobRepository(db),
        )
        with pytest.raises(JobNotFoundError):
            await svc.create(user_id=2, job_id=300)


async def test_application_creation_succeeds_for_public_job(engine) -> None:  # noqa: ANN001
    """ApplicationService.create succeeds for a public job (any user)."""
    sf = _session_factory(engine)
    async with sf() as db:
        await _create_user(db, user_id=1, email="a@test.com", name="User A")
        await _create_job(
            db,
            job_id=301,
            visibility="public",
            created_by_user_id=None,
        )

    async with sf() as db:
        svc = ApplicationService(
            repository=ApplicationRepository(db),
            job_repository=JobRepository(db),
        )
        result = await svc.create(user_id=1, job_id=301)
        assert result.job_id == 301
        assert result.status == "saved"
