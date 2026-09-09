"""Endpoint-level security tests — task ownership & cross-user denial.

Verifies that real FastAPI route handlers enforce:
- Task ownership: user A cannot poll user B's task (→ 404).
- Cross-user job workflow: user A cannot use user B's private/imported
  job for resume tailoring, cover letters, interview prep, etc. (→ 404).

Uses FastAPI TestClient with dependency overrides — no DB or Redis needed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_task_store
from app.api.v1 import jobs as jobs_module
from app.api.v1 import resume as resume_module
from app.domain.resume.lifecycle import ResumeLifecycleStatus
from app.domain.resume.provenance import build_resume_import_provenance
from app.domain.resume.schemas import ParsedProfile, ResumeResponse
from app.infrastructure.task_store import TaskStore
from app.models.user import User

# ── Helpers ────────────────────────────────────────────────────────────────


def _make_user(user_id: int, email: str = "test@example.com") -> User:
    """Create a minimal User object for dependency injection."""
    return User(
        id=user_id,
        email=email,
        name=f"User {user_id}",
        is_active=True,
    )


class FakeTaskStore:
    """In-memory TaskStore that mirrors the Redis contract."""

    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}
        self._counter = 0

    async def create(self, task_type: str, user_id: int) -> str:
        self._counter += 1
        task_id = f"fake-task-{self._counter}"
        self._tasks[task_id] = {
            "type": task_type,
            "status": "queued",
            "message": "Queued…",
            "user_id": user_id,
        }
        return task_id

    async def get(self, task_id: str) -> dict[str, Any] | None:
        return self._tasks.get(task_id)

    async def update(self, task_id: str, **fields: Any) -> None:
        if task_id in self._tasks:
            self._tasks[task_id].update(fields)


def _build_app_with_overrides(
    *,
    current_user: User,
    task_store: TaskStore | None = None,
) -> FastAPI:
    """Build a minimal FastAPI app with the jobs + resume routers and
    dependency overrides for the current user and task store."""
    from app.core.lifespan import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)

    # Include only the routers we need for these tests.
    app.include_router(jobs_module.router)
    app.include_router(resume_module.router)

    # Override auth dependency.
    async def _override_get_current_user() -> User:
        return current_user

    app.dependency_overrides[get_current_user] = _override_get_current_user

    # Override task store if provided.
    if task_store is not None:

        def _override_get_task_store() -> TaskStore:
            return task_store

        app.dependency_overrides[get_task_store] = _override_get_task_store

    return app


# ── Task Ownership Tests ────────────────────────────────────────────────────


class TestTaskOwnershipEndpoints:
    """Endpoint-level tests for /jobs/tasks/{task_id} and /resume/tasks/{task_id}."""

    @pytest.fixture
    def user_a(self) -> User:
        return _make_user(1, "a@example.com")

    @pytest.fixture
    def user_b(self) -> User:
        return _make_user(2, "b@example.com")

    @pytest.fixture
    def task_store(self) -> FakeTaskStore:
        return FakeTaskStore()

    # ── /jobs/tasks/{task_id} ────────────────────────────────────────────

    def test_user_can_poll_own_ingest_task(
        self, user_a: User, task_store: FakeTaskStore
    ) -> None:
        """User polls their own ingest task → 200 with task data."""
        import asyncio

        async def _setup() -> str:
            return await task_store.create("ingest", user_id=user_a.id)

        task_id = asyncio.run(_setup())

        app = _build_app_with_overrides(
            current_user=user_a, task_store=task_store  # type: ignore[arg-type]
        )
        client = TestClient(app)

        resp = client.get(f"/jobs/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "ingest"
        assert data["user_id"] == user_a.id

    def test_another_user_polling_ingest_task_returns_404(
        self, user_a: User, user_b: User, task_store: FakeTaskStore
    ) -> None:
        """User B polling user A's ingest task → 404."""
        import asyncio

        async def _setup() -> str:
            return await task_store.create("ingest", user_id=user_a.id)

        task_id = asyncio.run(_setup())

        app = _build_app_with_overrides(
            current_user=user_b, task_store=task_store  # type: ignore[arg-type]
        )
        client = TestClient(app)

        resp = client.get(f"/jobs/tasks/{task_id}")
        assert resp.status_code == 404

    def test_nonexistent_ingest_task_returns_404(
        self, user_a: User, task_store: FakeTaskStore
    ) -> None:
        """A task ID that doesn't exist → 404."""
        app = _build_app_with_overrides(
            current_user=user_a, task_store=task_store  # type: ignore[arg-type]
        )
        client = TestClient(app)

        resp = client.get("/jobs/tasks/nonexistent-id")
        assert resp.status_code == 404

    # ── /resume/tasks/{task_id} ──────────────────────────────────────────

    def test_user_can_poll_own_resume_task(
        self, user_a: User, task_store: FakeTaskStore
    ) -> None:
        """User polls their own resume task → 200."""
        import asyncio

        async def _setup() -> str:
            return await task_store.create("resume_parse", user_id=user_a.id)

        task_id = asyncio.run(_setup())

        app = _build_app_with_overrides(
            current_user=user_a, task_store=task_store  # type: ignore[arg-type]
        )
        client = TestClient(app)

        resp = client.get(f"/resume/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "resume_parse"
        assert data["user_id"] == user_a.id

    def test_user_can_poll_own_completed_resume_task_with_provenance(
        self, user_a: User, task_store: FakeTaskStore
    ) -> None:
        """The owner receives the complete serialized import result."""
        import asyncio

        provenance = build_resume_import_provenance("resume.pdf", []).model_dump(
            mode="json"
        )
        result = ResumeResponse(
            resume_id=7,
            filename="resume.pdf",
            is_active=True,
            lifecycle_status=ResumeLifecycleStatus.ACTIVE,
            profile=ParsedProfile(full_name="Ada Lovelace"),
            provenance=build_resume_import_provenance("resume.pdf", []),
        ).model_dump(mode="json")

        async def _setup() -> str:
            task_id = await task_store.create("resume_parse", user_id=user_a.id)
            await task_store.update(
                task_id, status="done", message="Parsed", result=result
            )
            return task_id

        task_id = asyncio.run(_setup())
        app = _build_app_with_overrides(
            current_user=user_a, task_store=task_store  # type: ignore[arg-type]
        )

        response = TestClient(app).get(f"/resume/tasks/{task_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "done"
        assert data["result"] == result
        assert data["result"]["provenance"] == provenance

    def test_another_user_cannot_poll_completed_resume_task_with_provenance(
        self, user_a: User, user_b: User, task_store: FakeTaskStore
    ) -> None:
        """Cross-user polling remains an indistinguishable not-found response."""
        import asyncio

        result = ResumeResponse(
            resume_id=7,
            filename="resume.pdf",
            is_active=True,
            lifecycle_status=ResumeLifecycleStatus.ACTIVE,
            profile=ParsedProfile(full_name="Ada Lovelace"),
            provenance=build_resume_import_provenance("resume.pdf", []),
        ).model_dump(mode="json")

        async def _setup() -> str:
            task_id = await task_store.create("resume_parse", user_id=user_a.id)
            await task_store.update(
                task_id, status="done", message="Parsed", result=result
            )
            return task_id

        task_id = asyncio.run(_setup())
        app = _build_app_with_overrides(
            current_user=user_b, task_store=task_store  # type: ignore[arg-type]
        )

        response = TestClient(app).get(f"/resume/tasks/{task_id}")

        assert response.status_code == 404
        assert response.json() == {"detail": "Task not found or expired"}

    def test_another_user_polling_resume_task_returns_404(
        self, user_a: User, user_b: User, task_store: FakeTaskStore
    ) -> None:
        """User B polling user A's resume task → 404."""
        import asyncio

        async def _setup() -> str:
            return await task_store.create("resume_parse", user_id=user_a.id)

        task_id = asyncio.run(_setup())

        app = _build_app_with_overrides(
            current_user=user_b, task_store=task_store  # type: ignore[arg-type]
        )
        client = TestClient(app)

        resp = client.get(f"/resume/tasks/{task_id}")
        assert resp.status_code == 404

    def test_nonexistent_resume_task_returns_404(
        self, user_a: User, task_store: FakeTaskStore
    ) -> None:
        """A resume task ID that doesn't exist → 404."""
        app = _build_app_with_overrides(
            current_user=user_a, task_store=task_store  # type: ignore[arg-type]
        )
        client = TestClient(app)

        resp = client.get("/resume/tasks/nonexistent-id")
        assert resp.status_code == 404

    def test_stale_task_without_user_id_returns_404(
        self, user_a: User, task_store: FakeTaskStore
    ) -> None:
        """A task stored without a user_id field → 404 (defense-in-depth)."""
        task_store._tasks["stale-task"] = {
            "type": "ingest",
            "status": "done",
            "message": "Done",
            # No user_id field
        }

        app = _build_app_with_overrides(
            current_user=user_a, task_store=task_store  # type: ignore[arg-type]
        )
        client = TestClient(app)

        resp = client.get("/jobs/tasks/stale-task")
        assert resp.status_code == 404


# ── Cross-User Job Workflow Denial Tests ────────────────────────────────────
# These tests verify that endpoint handlers call the service layer with the
# authenticated user's ID.  They mock the service layer to verify the user_id
# parameter is correctly passed and that cross-user access is denied where
# the service raises JobNotFoundError (→ 404).


def _make_app_with_mocked_services(
    user: User,
    job_service_mock: MagicMock | None = None,
    resume_service_mock: MagicMock | None = None,
    matching_service_mock: MagicMock | None = None,
    cover_letter_service_mock: MagicMock | None = None,
    interview_service_mock: MagicMock | None = None,
    application_service_mock: MagicMock | None = None,
) -> FastAPI:
    """Build an app with mocked domain services."""
    from app.api.deps import (
        get_application_service,
        get_cover_letter_service,
        get_interview_service,
        get_job_service,
        get_match_service,
        get_resume_service,
    )
    from app.core.lifespan import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)

    # Include routers
    from app.api.v1 import applications, cover_letters, interview, jobs, matches, resume

    app.include_router(jobs.router)
    app.include_router(resume.router)
    app.include_router(matches.router)
    app.include_router(cover_letters.router)
    app.include_router(interview.router)
    app.include_router(applications.router)

    # Override user
    async def _user_override() -> User:
        return user

    app.dependency_overrides[get_current_user] = _user_override

    # Override services
    if job_service_mock:

        def _job_svc() -> Any:
            return job_service_mock

        app.dependency_overrides[get_job_service] = _job_svc

    if resume_service_mock:

        def _resume_svc() -> Any:
            return resume_service_mock

        app.dependency_overrides[get_resume_service] = _resume_svc

    if matching_service_mock:

        def _match_svc() -> Any:
            return matching_service_mock

        app.dependency_overrides[get_match_service] = _match_svc

    if cover_letter_service_mock:

        def _cl_svc() -> Any:
            return cover_letter_service_mock

        app.dependency_overrides[get_cover_letter_service] = _cl_svc

    if interview_service_mock:

        def _iv_svc() -> Any:
            return interview_service_mock

        app.dependency_overrides[get_interview_service] = _iv_svc

    if application_service_mock:

        def _app_svc() -> Any:
            return application_service_mock

        app.dependency_overrides[get_application_service] = _app_svc

    return app


class TestCrossUserJobDenial:
    """Cross-user denial: user B cannot access user A's private job."""

    @pytest.fixture
    def user_a(self) -> User:
        return _make_user(1, "a@example.com")

    @pytest.fixture
    def user_b(self) -> User:
        return _make_user(2, "b@example.com")

    # ── GET /jobs/{job_id} ───────────────────────────────────────────────

    def test_get_job_cross_user_returns_404(
        self, user_a: User, user_b: User
    ) -> None:
        """User B tries to GET user A's private job → 404."""
        from app.domain.jobs.exceptions import JobNotFoundError

        mock_svc = MagicMock()
        mock_svc.get_job = AsyncMock(
            side_effect=JobNotFoundError(job_id=42)
        )

        app = _make_app_with_mocked_services(
            user=user_b, job_service_mock=mock_svc
        )
        client = TestClient(app)

        resp = client.get("/jobs/42")
        assert resp.status_code == 404
        # Verify service was called with user B's id
        mock_svc.get_job.assert_awaited_once_with(user_id=user_b.id, job_id=42)

    # ── GET /resume/tailor/{job_id} ──────────────────────────────────────

    def test_tailor_resume_cross_user_returns_404(
        self, user_a: User, user_b: User
    ) -> None:
        """User B tries to tailor resume for user A's private job → 404."""
        from app.domain.jobs.exceptions import JobNotFoundError

        mock_svc = MagicMock()
        mock_svc.tailor_for_job = AsyncMock(
            side_effect=JobNotFoundError(job_id=99)
        )

        app = _make_app_with_mocked_services(
            user=user_b, resume_service_mock=mock_svc
        )
        client = TestClient(app)

        resp = client.get("/resume/tailor/99")
        assert resp.status_code == 404
        call_kwargs = mock_svc.tailor_for_job.await_args.kwargs
        assert call_kwargs["user_id"] == user_b.id
        assert call_kwargs["job_id"] == 99

    # ── POST /cover-letters ──────────────────────────────────────────────

    def test_cover_letter_cross_user_returns_404(
        self, user_a: User, user_b: User
    ) -> None:
        """User B tries to generate cover letter for user A's private job → 404."""
        from app.domain.jobs.exceptions import JobNotFoundError

        mock_svc = MagicMock()
        mock_svc.generate = AsyncMock(
            side_effect=JobNotFoundError(job_id=99)
        )

        app = _make_app_with_mocked_services(
            user=user_b, cover_letter_service_mock=mock_svc
        )
        client = TestClient(app)

        resp = client.post(
            "/cover-letters",
            json={"job_id": 99},
        )
        assert resp.status_code == 404
        mock_svc.generate.assert_awaited_once_with(
            user_id=user_b.id, job_id=99, tone=None
        )

    # ── POST /interview-prep/{job_id} ────────────────────────────────────

    def test_interview_prep_cross_user_returns_404(
        self, user_a: User, user_b: User
    ) -> None:
        """User B tries interview prep for user A's private job → 404."""
        from app.domain.jobs.exceptions import JobNotFoundError

        mock_svc = MagicMock()
        mock_svc.get_or_generate = AsyncMock(
            side_effect=JobNotFoundError(job_id=99)
        )

        app = _make_app_with_mocked_services(
            user=user_b, interview_service_mock=mock_svc
        )
        client = TestClient(app)

        resp = client.post(
            "/interview-prep/99",
            json={},
        )
        assert resp.status_code == 404
        # Verify called with correct user and job
        call_kwargs = mock_svc.get_or_generate.await_args.kwargs
        assert call_kwargs["user_id"] == user_b.id
        assert call_kwargs["job_id"] == 99

    # ── GET /matches/{job_id}/explain ────────────────────────────────────

    def test_match_explanation_cross_user_returns_404(
        self, user_a: User, user_b: User
    ) -> None:
        """User B tries to get match explanation for user A's private job → 404."""
        from app.domain.jobs.exceptions import JobNotFoundError

        mock_svc = MagicMock()
        mock_svc.explain_match = AsyncMock(
            side_effect=JobNotFoundError(job_id=99)
        )

        app = _make_app_with_mocked_services(
            user=user_b, matching_service_mock=mock_svc
        )
        client = TestClient(app)

        resp = client.get("/matches/99/explain")
        assert resp.status_code == 404
        mock_svc.explain_match.assert_awaited_once_with(
            user_id=user_b.id, job_id=99
        )

    # ── POST /applications ───────────────────────────────────────────────

    def test_create_application_cross_user_returns_404(
        self, user_a: User, user_b: User
    ) -> None:
        """User B tries to create application for user A's private job → 404."""
        from app.domain.jobs.exceptions import JobNotFoundError

        mock_svc = MagicMock()
        mock_svc.create = AsyncMock(
            side_effect=JobNotFoundError(job_id=99)
        )

        app = _make_app_with_mocked_services(
            user=user_b, application_service_mock=mock_svc
        )
        client = TestClient(app)

        resp = client.post(
            "/applications",
            json={"job_id": 99},
        )
        assert resp.status_code == 404
        mock_svc.create.assert_awaited_once_with(
            user_id=user_b.id, job_id=99
        )

    def test_create_application_for_non_actionable_job_returns_409(
        self, user_a: User
    ) -> None:
        """A visible non-active job is a state conflict, not an authorization leak."""
        from app.domain.applications.exceptions import ApplicationJobNotActionableError

        mock_svc = MagicMock()
        mock_svc.create = AsyncMock(side_effect=ApplicationJobNotActionableError())
        app = _make_app_with_mocked_services(
            user=user_a, application_service_mock=mock_svc
        )
        client = TestClient(app)

        resp = client.post("/applications", json={"job_id": 99})
        assert resp.status_code == 409
        assert resp.json() == {"detail": "Job is no longer actionable"}
