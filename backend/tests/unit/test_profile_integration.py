"""Integration test for GET /resume/profile — real DB, real service, no mocks.

Verifies the full profile-read contract end-to-end:
- Authenticated user with no profile → 200 + null body.
- Authenticated user with a profile → 200 + valid ProfileResponse.
- Unauthenticated → 401 + exact error body.

Hard dependency: requires the test PostgreSQL database (job_app_test) to be
running and migrated.  The database is enforced in backend/tests/conftest.py.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_current_user
from app.api.v1 import resume as resume_module
from app.core.config import get_settings
from app.domain.resume.provenance import build_resume_import_provenance
from app.domain.resume.repository import ResumeRepository
from app.domain.resume.schemas import ParsedProfile
from app.domain.resume.service import ResumeService, build_summary_text
from app.infrastructure.database import get_db
from app.models.application import Application
from app.models.application_event import ApplicationEvent
from app.models.cover_letter import CoverLetter
from app.models.job import Job
from app.models.resume import CandidateProfile, Resume, TailoredResumeDraft
from app.models.user import User
from app.models.user_job_match import UserJobMatch

pytestmark = pytest.mark.integration


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def engine():
    """Module-scoped async engine pointing at the isolated test database."""
    settings = get_settings()
    return create_async_engine(settings.database_url, poolclass=NullPool, echo=False)


@pytest.fixture
def session_factory(engine):
    """Per-test session factory — fresh connections, clean event-loop lifecycle."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def clean_db(engine) -> None:  # noqa: ANN001
    """Truncate profile-related tables before each test."""
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        for table in ("candidate_profiles", "resumes", "users"):
            await db.execute(
                text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"),
            )
        await db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────


def _build_app(
    *, current_user: User | None = None, session_factory=None
) -> FastAPI:
    """Build a minimal FastAPI app with only the resume router.

    If *current_user* is provided, overrides ``get_current_user`` to
    skip JWT validation.  If omitted, the real auth dependency runs and
    will reject unauthenticated requests with 401.
    """
    from app.core.lifespan import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(resume_module.router)

    if current_user is not None:

        async def _override_get_current_user() -> User:
            return current_user

        app.dependency_overrides[get_current_user] = _override_get_current_user

    if session_factory is not None:

        async def _override_get_db():
            async with session_factory() as db:
                yield db

        app.dependency_overrides[get_db] = _override_get_db

    return app


async def _seed_user(db: AsyncSession, *, user_id: int = 1) -> User:
    """Insert a test user and return the ORM object."""
    user = User(
        id=user_id,
        email=f"user{user_id}@test.com",
        name=f"User {user_id}",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _seed_profile(
    db: AsyncSession,
    *,
    user_id: int = 1,
    full_name: str = "Test User",
) -> CandidateProfile:
    """Insert an active resume + candidate profile for an existing user."""
    resume = Resume(
        user_id=user_id, filename="test.pdf", raw_text="dummy", is_active=True,
    )
    db.add(resume)
    await db.flush()

    parsed = ParsedProfile(
        full_name=full_name,
        email=f"user{user_id}@test.com",
        headline="Software Engineer",
        skills=["Python", "FastAPI"],
        languages=[{"language": "English", "proficiency": "Fluent"}],
        licenses=[{"name": "Registered Engineer", "issuer": "State Board"}],
        certifications=["AWS Certified Developer"],
        projects=[{"name": "Candidate Portal", "technologies": ["Python"]}],
        years_experience=3,
    )
    profile = CandidateProfile(
        user_id=user_id,
        resume_id=resume.id,
        parsed=parsed.model_dump(),
        summary_text=build_summary_text(parsed),
        embedding=[0.0] * 1536,
    )
    db.add(profile)
    await db.flush()
    return profile


async def _seed_import_draft(
    db: AsyncSession,
    *,
    user_id: int,
    full_name: str,
    lifecycle_status: str = "draft",
    is_active: bool = False,
) -> Resume:
    filename = f"{full_name.lower().replace(' ', '-')}.pdf"
    resume = Resume(
        user_id=user_id,
        filename=filename,
        raw_text="draft text",
        is_active=is_active,
        lifecycle_status=lifecycle_status,
        import_provenance=build_resume_import_provenance(
            filename, []
        ).model_dump(mode="json"),
    )
    db.add(resume)
    await db.flush()
    parsed = ParsedProfile(full_name=full_name, headline="Engineer")
    db.add(
        CandidateProfile(
            user_id=user_id,
            resume_id=resume.id,
            parsed=parsed.model_dump(),
            summary_text="Engineer",
            embedding=[0.0] * 1536,
        )
    )
    await db.flush()
    return resume


# ── Tests ─────────────────────────────────────────────────────────────────


class TestProfileIntegration:
    """End-to-end contract tests through the real route handler + real DB."""

    @pytest.mark.asyncio
    async def test_replacement_import_is_draft_and_active_profile_is_preserved(
        self, session_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: ANN001
        """A persisted replacement draft cannot change active profile reads."""
        async with session_factory() as db:
            user = await _seed_user(db)
            await _seed_profile(db, user_id=user.id, full_name="Original User")
            other_user = await _seed_user(db, user_id=2)
            await _seed_profile(db, user_id=other_user.id, full_name="Other User")
            await db.commit()

            replacement = ParsedProfile(full_name="Replacement User", headline="New")
            monkeypatch.setattr(
                "app.domain.resume.service.extract_text_from_bytes",
                lambda *_: "replacement text",
            )

            async def parse(_: str) -> ParsedProfile:
                return replacement

            async def embed(_: str) -> list[float]:
                return [0.1] * 1536

            monkeypatch.setattr("app.domain.resume.service.parse_resume_text", parse)
            monkeypatch.setattr("app.domain.resume.service.embed_text", embed)
            result = await ResumeService(ResumeRepository(db)).upload_and_parse(
                user_id=user.id, filename="replacement.pdf", content=b"pdf"
            )
            await db.commit()

            active = await ResumeService(ResumeRepository(db)).get_profile(user.id)
            other_active = await ResumeService(ResumeRepository(db)).get_profile(
                other_user.id
            )
            resumes = (
                await db.execute(
                    text(
                        "SELECT is_active, lifecycle_status FROM resumes "
                        "WHERE user_id = :user_id ORDER BY id"
                    ),
                    {"user_id": user.id},
                )
            ).all()

        assert result.is_active is False
        assert result.lifecycle_status.value == "draft"
        assert active.profile.full_name == "Original User"
        assert other_active.profile.full_name == "Other User"
        assert resumes == [(True, "active"), (False, "draft")]

    # ── unauthenticated → 401 ──────────────────────────────────────────

    def test_unauthenticated_returns_401(self) -> None:
        """No auth override → the real auth dependency rejects the request."""
        app = _build_app()  # current_user=None → no auth override
        client = TestClient(app)

        resp = client.get("/resume/profile")

        assert resp.status_code == 401, (
            f"Expected 401 for unauthenticated, got {resp.status_code}. "
            f"Body: {resp.text}"
        )
        assert resp.json() == {"detail": "Not authenticated"}, (
            f"Expected exact 401 body, got: {resp.json()}"
        )

    # ── no profile → 200 + null, has profile → 200 + data ────────────
    #
    # NOTE: The two 200 scenarios share one async test method.
    # On Windows, pytest-asyncio + asyncpg + ProactorEventLoop have a
    # known incompatibility that prevents running multiple async TestClient
    # tests in the same module (all existing integration tests in this
    # repo share the same limitation).  On Linux / macOS / Docker the
    # tests pass without issue.  The 401 test above is synchronous and
    # runs everywhere.

    async def test_profile_read_contract(
        self, session_factory,
    ) -> None:  # noqa: ANN001
        """Authenticated user: no profile → 200+null, then seed profile →
        200+valid payload."""

        # ── no profile ────────────────────────────────────────────────
        async with session_factory() as db:
            user = await _seed_user(db)
            await db.commit()

        app = _build_app(current_user=user)
        with TestClient(app) as client:

            resp = client.get("/resume/profile")
            assert resp.status_code == 200, (
                f"Expected 200 for empty profile, got {resp.status_code}. "
                f"Body: {resp.text}"
            )
            assert resp.json() is None, (
                f"Expected null body, got: {resp.text}"
            )

            # ── has profile ───────────────────────────────────────────────
            async with session_factory() as db:
                await _seed_profile(db, user_id=user.id)
                await db.commit()

            resp = client.get("/resume/profile")
            assert resp.status_code == 200, (
                f"Expected 200, got {resp.status_code}. Body: {resp.text}"
            )
            data = resp.json()
            assert data is not None, "Expected non-null profile data"
            assert isinstance(data["profile_id"], int)
            assert isinstance(data["resume_id"], int)
            assert data["profile"]["full_name"] == "Test User"
            assert data["profile"]["headline"] == "Software Engineer"
            assert "Python" in data["profile"]["skills"]
            assert data["profile"]["languages"] == [
                {"language": "English", "proficiency": "Fluent"}
            ]
            assert data["profile"]["licenses"] == [
                {"name": "Registered Engineer", "issuer": "State Board"}
            ]

    @pytest.mark.asyncio
    async def test_profile_update_preserves_existing_sections_and_licenses(
        self, session_factory
    ) -> None:  # noqa: ANN001
        async with session_factory() as db:
            user = await _seed_user(db)
            await _seed_profile(db, user_id=user.id)
            await db.commit()

        with TestClient(
            _build_app(current_user=user, session_factory=session_factory)
        ) as client:
            profile = client.get("/resume/profile").json()["profile"]
            existing_projects = profile["projects"]
            profile["languages"] = [
                {"language": "Spanish", "proficiency": "Professional"}
            ]
            profile["licenses"] = [
                {"name": "Safety Registration", "issuer": "Safety Board"}
            ]

            response = client.put("/resume/profile", json={"profile": profile})

            assert response.status_code == 200
            updated = response.json()["profile"]
            assert updated["languages"] == [
                {"language": "Spanish", "proficiency": "Professional"}
            ]
            assert updated["licenses"] == [
                {"name": "Safety Registration", "issuer": "Safety Board"}
            ]
            assert updated["certifications"] == ["AWS Certified Developer"]
            assert updated["projects"] == existing_projects
            assert client.get("/resume/profile").json()["profile"] == updated

    @pytest.mark.asyncio
    async def test_import_list_and_detail_are_owned_current_drafts_only(
        self, session_factory
    ) -> None:  # noqa: ANN001
        async with session_factory() as db:
            user = await _seed_user(db)
            active = await _seed_profile(db, user_id=user.id, full_name="Active User")
            draft = await _seed_import_draft(
                db, user_id=user.id, full_name="Pending User"
            )
            inactive = await _seed_import_draft(
                db,
                user_id=user.id,
                full_name="Rejected User",
                lifecycle_status="inactive",
            )
            other = await _seed_user(db, user_id=2)
            foreign = await _seed_import_draft(
                db, user_id=other.id, full_name="Foreign User"
            )
            await db.commit()

        with TestClient(
            _build_app(current_user=user, session_factory=session_factory)
        ) as client:
            response = client.get("/resume/imports")
            assert response.status_code == 200
            assert [item["resume_id"] for item in response.json()] == [draft.id]

            detail = client.get(f"/resume/imports/{draft.id}")
            assert detail.status_code == 200
            assert detail.json()["profile"]["full_name"] == "Pending User"
            assert detail.json()["provenance"]["extraction_warning_codes"] == []

            for resume_id in (active.resume_id, inactive.id, foreign.id, 999999):
                assert client.get(f"/resume/imports/{resume_id}").status_code == 404

    @pytest.mark.asyncio
    async def test_approve_import_switches_active_and_dispatches_after_commit(
        self, session_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: ANN001
        events: list[str] = []

        async def record_ranking(user_id: int) -> None:
            events.append(f"ranking:{user_id}")

        async def embed(_: str) -> list[float]:
            return [0.2] * 1536

        monkeypatch.setattr(resume_module, "_enqueue_match_ranking", record_ranking)
        monkeypatch.setattr("app.domain.resume.service.embed_text", embed)
        async with session_factory() as db:
            user = await _seed_user(db)
            old = await _seed_profile(db, user_id=user.id, full_name="Old User")
            first_draft = await _seed_import_draft(
                db, user_id=user.id, full_name="Approved User"
            )
            original_provenance = build_resume_import_provenance(
                first_draft.filename, []
            ).model_dump(mode="json")
            first_draft.import_provenance = original_provenance
            second_draft = await _seed_import_draft(
                db, user_id=user.id, full_name="Still Pending"
            )
            inactive = await _seed_import_draft(
                db,
                user_id=user.id,
                full_name="Already Rejected",
                lifecycle_status="inactive",
            )
            other_user = await _seed_user(db, user_id=2)
            foreign = await _seed_import_draft(
                db, user_id=other_user.id, full_name="Foreign Draft"
            )

            automatic_job = Job(
                fingerprint="cleanup-auto",
                title="Automatic match",
                company="Acme",
                source="test",
                source_url="https://example.com/automatic",
                raw_response={},
                description="Automatic match description",
            )
            manual_job = Job(
                fingerprint="cleanup-manual",
                title="Manual match",
                company="Beta",
                source="test",
                source_url="https://example.com/manual",
                raw_response={},
                description="Manual match description",
            )
            db.add_all([automatic_job, manual_job])
            await db.flush()
            cover_letter = CoverLetter(
                user_id=user.id,
                job_id=automatic_job.id,
                content="Saved cover letter",
                model="test-model",
            )
            db.add(cover_letter)
            await db.flush()
            application = Application(
                user_id=user.id,
                job_id=automatic_job.id,
                status="applied",
                cover_letter_id=cover_letter.id,
            )
            db.add(application)
            await db.flush()
            db.add(
                ApplicationEvent(
                    user_id=user.id,
                    application_id=application.id,
                    job_id=automatic_job.id,
                    event_type="submitted",
                )
            )
            db.add_all(
                [
                    UserJobMatch(
                        user_id=user.id,
                        job_id=automatic_job.id,
                        vector_score=90.0,
                        origin="automatic",
                    ),
                    UserJobMatch(
                        user_id=user.id,
                        job_id=manual_job.id,
                        vector_score=80.0,
                        origin="manual",
                    ),
                    TailoredResumeDraft(
                        user_id=user.id,
                        job_id=automatic_job.id,
                        source_profile_id=old.id,
                        job_title="Automatic match",
                        job_company="Acme",
                        content={"full_name": "Saved snapshot"},
                    ),
                ]
            )
            await db.commit()

        with TestClient(
            _build_app(current_user=user, session_factory=session_factory)
        ) as client:
            response = client.post(f"/resume/imports/{first_draft.id}/approve")
            assert response.status_code == 200
            assert response.json()["profile"]["full_name"] == "Approved User"
            profile = client.get("/resume/profile")
            assert profile.json()["profile"]["full_name"] == "Approved User"
            edited = client.put(
                "/resume/profile",
                json={"profile": {"full_name": "Edited User", "headline": "Changed"}},
            )
            assert edited.status_code == 200
            assert edited.json()["profile"]["full_name"] == "Edited User"
            for resume_id in (inactive.id, foreign.id, 999999):
                assert client.post(f"/resume/imports/{resume_id}/approve").status_code == 404

        async with session_factory() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT id, is_active, lifecycle_status FROM resumes "
                        "WHERE user_id = :user_id ORDER BY id"
                    ),
                    {"user_id": user.id},
                )
            ).all()

        assert rows == [
            (first_draft.id, True, "active"),
            (second_draft.id, False, "draft"),
            (inactive.id, False, "inactive"),
        ]
        assert events == [f"ranking:{user.id}", f"ranking:{user.id}"]

        async with session_factory() as db:
            old_profile = await db.get(CandidateProfile, old.id)
            assert old_profile is None
            old_resume = await db.get(Resume, old.resume_id)
            assert old_resume is None
            snapshot = (
                await db.execute(
                    text(
                        "SELECT source_profile_id, content FROM tailored_resume_drafts "
                        "WHERE user_id = :user_id"
                    ),
                    {"user_id": user.id},
                )
            ).one()
            assert snapshot[0] is None
            assert snapshot[1] == {"full_name": "Saved snapshot"}
            assert (
                await db.execute(
                    text(
                        "SELECT COUNT(*) FROM applications WHERE user_id = :user_id"
                    ),
                    {"user_id": user.id},
                )
            ).scalar_one() == 1
            assert (
                await db.execute(
                    text(
                        "SELECT COUNT(*) FROM cover_letters WHERE user_id = :user_id"
                    ),
                    {"user_id": user.id},
                )
            ).scalar_one() == 1
            assert (
                await db.execute(
                    text(
                        "SELECT COUNT(*) FROM application_events WHERE user_id = :user_id"
                    ),
                    {"user_id": user.id},
                )
            ).scalar_one() == 1
            match_rows = (
                await db.execute(
                    text(
                        "SELECT origin FROM user_job_matches WHERE user_id = :user_id "
                        "ORDER BY origin"
                    ),
                    {"user_id": user.id},
                )
            ).scalars().all()
            assert match_rows == ["automatic", "manual"]
            stored_provenance = (
                await db.execute(
                    text("SELECT import_provenance FROM resumes WHERE id = :resume_id"),
                    {"resume_id": first_draft.id},
                )
            ).scalar_one()
        assert stored_provenance == original_provenance

    @pytest.mark.asyncio
    async def test_reject_import_keeps_active_and_does_not_dispatch(
        self, session_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: ANN001
        events: list[int] = []

        async def record_ranking(user_id: int) -> None:
            events.append(user_id)

        monkeypatch.setattr(resume_module, "_enqueue_match_ranking", record_ranking)
        async with session_factory() as db:
            user = await _seed_user(db)
            active = await _seed_profile(db, user_id=user.id, full_name="Active User")
            draft = await _seed_import_draft(
                db, user_id=user.id, full_name="Rejected User"
            )
            original_provenance = deepcopy(draft.import_provenance)
            assert original_provenance is not None
            draft.import_provenance = build_resume_import_provenance(
                draft.filename, []
            ).model_dump(mode="json")
            other_user = await _seed_user(db, user_id=2)
            foreign = await _seed_import_draft(
                db, user_id=other_user.id, full_name="Foreign Draft"
            )
            await db.commit()

        with TestClient(
            _build_app(current_user=user, session_factory=session_factory)
        ) as client:
            response = client.post(f"/resume/imports/{draft.id}/reject")
            assert response.status_code == 204
            assert client.get("/resume/imports").json() == []
            assert client.get("/resume/profile").json()["profile"]["full_name"] == (
                "Active User"
            )
            for resume_id in (draft.id, active.resume_id, foreign.id, 999999):
                assert client.post(f"/resume/imports/{resume_id}/reject").status_code == 404

        assert events == []

    @pytest.mark.asyncio
    async def test_approved_retry_is_idempotent_and_does_not_delete_new_source(
        self, session_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: ANN001
        events: list[int] = []

        async def record_ranking(user_id: int) -> None:
            events.append(user_id)

        monkeypatch.setattr(resume_module, "_enqueue_match_ranking", record_ranking)
        async with session_factory() as db:
            user = await _seed_user(db)
            old = await _seed_profile(db, user_id=user.id, full_name="Old User")
            draft = await _seed_import_draft(
                db, user_id=user.id, full_name="Replacement User"
            )
            await db.commit()

        with TestClient(
            _build_app(current_user=user, session_factory=session_factory)
        ) as client:
            first = client.post(f"/resume/imports/{draft.id}/approve")
            second = client.post(f"/resume/imports/{draft.id}/approve")
            assert first.status_code == 200
            assert second.status_code == 200
            assert second.json()["profile"]["full_name"] == "Replacement User"

        async with session_factory() as db:
            assert await db.get(Resume, old.resume_id) is None
            assert await db.get(CandidateProfile, old.id) is None
            assert (
                await db.execute(
                    text("SELECT COUNT(*) FROM resumes WHERE user_id = :user_id"),
                    {"user_id": user.id},
                )
            ).scalar_one() == 1
            assert (
                await db.execute(
                    text(
                        "SELECT COUNT(*) FROM candidate_profiles WHERE user_id = :user_id"
                    ),
                    {"user_id": user.id},
                )
            ).scalar_one() == 1
        assert events == [user.id, user.id]

    @pytest.mark.asyncio
    async def test_multiple_active_sources_fail_closed_and_roll_back(
        self, session_factory
    ) -> None:  # noqa: ANN001
        async with session_factory() as db:
            user = await _seed_user(db)
            first = await _seed_profile(db, user_id=user.id, full_name="First Active")
            second = await _seed_profile(db, user_id=user.id, full_name="Second Active")
            draft = await _seed_import_draft(
                db, user_id=user.id, full_name="Replacement User"
            )
            await db.commit()

        with TestClient(
            _build_app(current_user=user, session_factory=session_factory)
        ) as client:
            with pytest.raises(ValueError, match="Multiple active resumes"):
                client.post(f"/resume/imports/{draft.id}/approve")

        async with session_factory() as db:
            states = (
                await db.execute(
                    text(
                        "SELECT id, is_active, lifecycle_status FROM resumes "
                        "WHERE user_id = :user_id ORDER BY id"
                    ),
                    {"user_id": user.id},
                )
            ).all()
            assert states == [
                (first.resume_id, True, "active"),
                (second.resume_id, True, "active"),
                (draft.id, False, "draft"),
            ]

    @pytest.mark.asyncio
    async def test_active_resume_with_inconsistent_lifecycle_fails_closed(
        self, session_factory
    ) -> None:  # noqa: ANN001
        async with session_factory() as db:
            user = await _seed_user(db)
            active = await _seed_profile(db, user_id=user.id, full_name="Active User")
            active_resume = await db.get(Resume, active.resume_id)
            assert active_resume is not None
            active_resume.lifecycle_status = "draft"
            draft = await _seed_import_draft(
                db, user_id=user.id, full_name="Replacement User"
            )
            await db.commit()

        with TestClient(
            _build_app(current_user=user, session_factory=session_factory)
        ) as client:
            with pytest.raises(ValueError, match="inconsistent lifecycle"):
                client.post(f"/resume/imports/{draft.id}/approve")

        async with session_factory() as db:
            states = (
                await db.execute(
                    text(
                        "SELECT id, is_active, lifecycle_status FROM resumes "
                        "WHERE user_id = :user_id ORDER BY id"
                    ),
                    {"user_id": user.id},
                )
            ).all()
            assert states == [
                (active.resume_id, True, "draft"),
                (draft.id, False, "draft"),
            ]
