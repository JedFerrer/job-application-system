"""Regression tests for the GET /resume/profile empty-state contract.

Verifies:
- Authenticated user with no profile → 200 + null body (NOT 404).
- Authenticated user with a profile → 200 + valid ProfileResponse.
- Unauthenticated → 401.

Uses FastAPI TestClient with dependency overrides — no DB or Redis needed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_resume_service
from app.api.v1 import resume as resume_module
from app.domain.resume.exceptions import ProfileNotFoundError
from app.domain.resume.schemas import ParsedProfile, ProfileResponse
from app.domain.resume.service import ResumeService
from app.models.user import User

pytestmark = pytest.mark.unit


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_user(user_id: int = 1, email: str = "test@example.com") -> User:
    return User(
        id=user_id,
        email=email,
        name=f"User {user_id}",
        is_active=True,
    )


def _make_profile_response(user_id: int = 1) -> ProfileResponse:
    return ProfileResponse(
        profile_id=10,
        resume_id=20,
        profile=ParsedProfile(
            full_name="Test User",
            email="test@example.com",
            headline="Software Engineer",
            skills=["Python", "FastAPI"],
            years_experience=3,
        ),
    )


def _build_app(
    *,
    current_user: User | None = None,
    resume_service: ResumeService | None = None,
) -> FastAPI:
    """Build a minimal FastAPI app with only the resume router and
    optional dependency overrides for auth and the resume service."""
    from app.core.lifespan import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(resume_module.router)

    if current_user is not None:

        async def _override_get_current_user() -> User:
            return current_user

        app.dependency_overrides[get_current_user] = _override_get_current_user

    if resume_service is not None:

        def _override_get_resume_service() -> ResumeService:
            return resume_service

        app.dependency_overrides[get_resume_service] = _override_get_resume_service

    return app


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def authenticated_user() -> User:
    return _make_user(1, "authed@example.com")


@pytest.fixture
def mock_service_with_profile() -> ResumeService:
    """Mock ResumeService whose get_profile returns a valid profile."""
    svc = AsyncMock(spec=ResumeService)
    svc.get_profile = AsyncMock(return_value=_make_profile_response())
    return svc


@pytest.fixture
def mock_service_no_profile() -> ResumeService:
    """Mock ResumeService whose get_profile raises ProfileNotFoundError."""
    svc = AsyncMock(spec=ResumeService)
    svc.get_profile = AsyncMock(side_effect=ProfileNotFoundError(user_id=1))
    return svc


# ── Tests ──────────────────────────────────────────────────────────────────


class TestProfileEmptyStateContract:
    """Guarantees the profile-read contract: 200+null for empty, 200+data for
    populated, 401 for unauthenticated."""

    # ── Case 1: authenticated, no profile → 200 + null ─────────────────

    def test_authenticated_no_profile_returns_200_null(
        self,
        authenticated_user: User,
        mock_service_no_profile: ResumeService,
    ) -> None:
        """When the user has no active profile, the endpoint returns HTTP 200
        with a null body — NOT a 404 that would pollute the browser console."""
        app = _build_app(
            current_user=authenticated_user,
            resume_service=mock_service_no_profile,
        )
        client = TestClient(app)

        resp = client.get("/resume/profile")

        assert resp.status_code == 200, (
            f"Expected 200 for empty profile, got {resp.status_code}. "
            f"Body: {resp.text}"
        )
        assert resp.json() is None, (
            f"Expected null body, got: {resp.text}"
        )

    # ── Case 2: authenticated, has profile → 200 + valid payload ────────

    def test_authenticated_with_profile_returns_200_and_data(
        self,
        authenticated_user: User,
        mock_service_with_profile: ResumeService,
    ) -> None:
        """When the user has an active profile, the endpoint returns HTTP 200
        with a valid ProfileResponse payload."""
        app = _build_app(
            current_user=authenticated_user,
            resume_service=mock_service_with_profile,
        )
        client = TestClient(app)

        resp = client.get("/resume/profile")

        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}. Body: {resp.text}"
        )
        data = resp.json()
        assert data is not None, "Expected non-null profile data"

        # Top-level keys and types
        assert isinstance(data["profile_id"], int)
        assert data["profile_id"] == 10
        assert isinstance(data["resume_id"], int)
        assert data["resume_id"] == 20

        # Profile sub-object
        profile = data["profile"]
        assert isinstance(profile, dict)
        assert profile["full_name"] == "Test User"
        assert profile["headline"] == "Software Engineer"
        assert isinstance(profile["skills"], list)
        assert "Python" in profile["skills"]
        assert isinstance(profile["work_history"], list)
        assert isinstance(profile["education"], list)
        assert isinstance(profile["years_experience"], (int, float))

    # ── Case 3: unauthenticated → 401 ──────────────────────────────────

    def test_unauthenticated_returns_401(self) -> None:
        """Requests without a valid auth token must return 401, not 404 or
        any other status that could be confused with an empty profile."""
        app = _build_app()  # no auth override → token validation fails
        client = TestClient(app)

        resp = client.get("/resume/profile")

        assert resp.status_code == 401, (
            f"Expected 401 for unauthenticated, got {resp.status_code}. "
            f"Body: {resp.text}"
        )
        data = resp.json()
        assert data == {"detail": "Not authenticated"}, (
            f"Expected exact 401 body, got: {data}"
        )

    # ── Edge: profile_ids differ across users ──────────────────────────

    def test_profile_response_shape_is_consistent(
        self,
        authenticated_user: User,
        mock_service_with_profile: ResumeService,
    ) -> None:
        """The ProfileResponse JSON shape matches the frontend TypeScript
        ProfileResponse interface."""
        app = _build_app(
            current_user=authenticated_user,
            resume_service=mock_service_with_profile,
        )
        client = TestClient(app)

        resp = client.get("/resume/profile")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}. Body: {resp.text}"
        )
        data = resp.json()

        # Required top-level keys and types
        assert isinstance(data["profile_id"], int)
        assert isinstance(data["resume_id"], int)
        assert isinstance(data["profile"], dict)
        # Profile sub-object: assert key presence first, then validate type.
        # Using .get() alone would silently pass if a key goes missing —
        # key-presence assertions catch shape regressions.
        profile = data["profile"]
        assert "full_name" in profile
        assert isinstance(profile["full_name"], (str, type(None)))
        assert "email" in profile
        assert isinstance(profile["email"], (str, type(None)))
        assert "headline" in profile
        assert isinstance(profile["headline"], (str, type(None)))
        assert "skills" in profile
        assert isinstance(profile["skills"], list)
        assert "work_history" in profile
        assert isinstance(profile["work_history"], list)
        assert "education" in profile
        assert isinstance(profile["education"], list)
        assert "certifications" in profile
        assert isinstance(profile["certifications"], list)
        assert "years_experience" in profile
        assert isinstance(profile["years_experience"], (int, float, type(None)))
        assert "seniority" in profile
        assert isinstance(profile["seniority"], (str, type(None)))
