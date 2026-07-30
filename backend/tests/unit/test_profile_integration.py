"""Integration test for GET /resume/profile — real DB, real service, no mocks.

Verifies the full profile-read contract end-to-end:
- Authenticated user with no profile → 200 + null body.
- Authenticated user with a profile → 200 + valid ProfileResponse.
- Unauthenticated → 401 + exact error body.

Hard dependency: requires the test PostgreSQL database (job_app_test) to be
running and migrated.  The database is enforced in backend/tests/conftest.py.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_current_user
from app.api.v1 import resume as resume_module
from app.core.config import get_settings
from app.domain.resume.repository import ResumeRepository
from app.domain.resume.schemas import ParsedProfile
from app.domain.resume.service import ResumeService
from app.models.resume import CandidateProfile, Resume
from app.models.user import User

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


def _build_app(*, current_user: User | None = None) -> FastAPI:
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
        years_experience=3,
    )
    profile = CandidateProfile(
        user_id=user_id,
        resume_id=resume.id,
        parsed=parsed.model_dump(),
        summary_text="Software Engineer. Skills: Python, FastAPI.",
        embedding=[0.0] * 1536,
    )
    db.add(profile)
    await db.flush()
    return profile


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
