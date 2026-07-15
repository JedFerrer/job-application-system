"""Focused tests for saved tailored resume draft CRUD behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_job_repository, get_resume_service
from app.api.v1 import resume as resume_module
from app.core.lifespan import register_exception_handlers
from app.domain.jobs.exceptions import JobNotFoundError
from app.domain.resume.exceptions import (
    ProfileNotFoundError,
    TailoredResumeDraftNotFoundError,
)
from app.domain.resume.schemas import (
    TailoredResume,
    TailoredResumeDraftCreate,
    TailoredResumeDraftListItem,
    TailoredResumeDraftResponse,
)
from app.domain.resume.service import ResumeService
from app.models.user import User

pytestmark = pytest.mark.unit


def _content() -> dict[str, object]:
    return {
        "job_id": 11,
        "professional_summary": "Backend Engineer",
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


def _draft() -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=4,
        job_id=11,
        source_profile_id=8,
        job_title="Backend Engineer",
        job_company="Acme",
        content=_content(),
        created_at=now,
        updated_at=now,
    )


def _response(*, draft_id: int = 4, job_id: int = 11) -> TailoredResumeDraftResponse:
    now = datetime.now(UTC)
    content = TailoredResume.model_validate(_content() | {"job_id": job_id})
    return TailoredResumeDraftResponse(
        id=draft_id,
        job_id=job_id,
        source_profile_id=8,
        job_title="Backend Engineer",
        job_company="Acme",
        created_at=now,
        updated_at=now,
        content=content,
    )


def _list_item(*, draft_id: int, title: str) -> TailoredResumeDraftListItem:
    now = datetime.now(UTC)
    return TailoredResumeDraftListItem(
        id=draft_id,
        job_id=11,
        source_profile_id=8,
        job_title=title,
        job_company="Acme",
        created_at=now,
        updated_at=now,
    )


def _build_route_app(
    *, service: AsyncMock, job_repository: AsyncMock, user_id: int = 7,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(resume_module.router)

    async def current_user() -> User:
        return User(
            id=user_id,
            email=f"user{user_id}@example.com",
            name=f"User {user_id}",
            is_active=True,
        )

    def resume_service() -> AsyncMock:
        return service

    def jobs() -> AsyncMock:
        return job_repository

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_resume_service] = resume_service
    app.dependency_overrides[get_job_repository] = jobs
    return app


def _payload(*, job_id: int = 11) -> dict[str, object]:
    return {"job_id": job_id, "content": _content() | {"job_id": job_id}}


@pytest.mark.asyncio
async def test_create_draft_persists_job_and_profile_snapshot() -> None:
    repository = AsyncMock()
    repository.get_active_profile.return_value = SimpleNamespace(
        id=8, parsed={}
    )
    repository.create_tailored_draft.return_value = _draft()
    jobs = AsyncMock()
    jobs.get_visible_by_id.return_value = SimpleNamespace(
        title="Backend Engineer", company="Acme"
    )
    service = ResumeService(repository)

    result = await service.create_tailored_draft(
        user_id=7,
        payload=TailoredResumeDraftCreate(job_id=11, content=_content()),
        job_repository=jobs,
    )

    assert result.content.job_id == 11
    repository.create_tailored_draft.assert_awaited_once()
    kwargs = repository.create_tailored_draft.await_args.kwargs
    assert kwargs["user_id"] == 7
    assert kwargs["job_id"] == 11
    assert kwargs["source_profile_id"] == 8
    assert kwargs["content"] == _content()


@pytest.mark.asyncio
async def test_create_draft_requires_active_profile() -> None:
    repository = AsyncMock()
    repository.get_active_profile.return_value = None
    service = ResumeService(repository)

    with pytest.raises(ProfileNotFoundError):
        await service.create_tailored_draft(
            user_id=7,
            payload=TailoredResumeDraftCreate(job_id=11, content=_content()),
            job_repository=AsyncMock(),
        )


@pytest.mark.asyncio
async def test_create_draft_requires_visible_job() -> None:
    repository = AsyncMock()
    repository.get_active_profile.return_value = SimpleNamespace(id=8)
    jobs = AsyncMock()
    jobs.get_visible_by_id.return_value = None
    service = ResumeService(repository)

    with pytest.raises(JobNotFoundError):
        await service.create_tailored_draft(
            user_id=7,
            payload=TailoredResumeDraftCreate(job_id=11, content=_content()),
            job_repository=jobs,
        )


@pytest.mark.asyncio
async def test_list_is_metadata_only_and_get_delete_are_user_scoped() -> None:
    repository = AsyncMock()
    draft = _draft()
    repository.list_tailored_drafts.return_value = [draft]
    repository.get_tailored_draft.return_value = draft
    repository.delete_tailored_draft.return_value = True
    service = ResumeService(repository)

    listed = await service.list_tailored_drafts(user_id=7)
    assert listed[0].job_title == "Backend Engineer"
    assert not hasattr(listed[0], "content")

    detail = await service.get_tailored_draft(user_id=7, draft_id=4)
    assert detail.content.job_id == 11
    await service.delete_tailored_draft(user_id=7, draft_id=4)
    repository.get_tailored_draft.assert_awaited_once_with(user_id=7, draft_id=4)
    repository.delete_tailored_draft.assert_awaited_once_with(user_id=7, draft_id=4)


@pytest.mark.asyncio
async def test_missing_draft_uses_not_found_behavior() -> None:
    repository = AsyncMock()
    repository.get_tailored_draft.return_value = None
    repository.delete_tailored_draft.return_value = False
    service = ResumeService(repository)

    with pytest.raises(TailoredResumeDraftNotFoundError):
        await service.get_tailored_draft(user_id=7, draft_id=99)
    with pytest.raises(TailoredResumeDraftNotFoundError):
        await service.delete_tailored_draft(user_id=7, draft_id=99)


class TestDraftRouteContract:
    def test_create_valid_request_returns_201_and_exact_snapshot(self) -> None:
        service = AsyncMock(spec=ResumeService)
        service.create_tailored_draft.return_value = _response()
        jobs = AsyncMock()
        response = TestClient(_build_route_app(service=service, job_repository=jobs)).post(
            "/resume/drafts", json=_payload()
        )

        assert response.status_code == 201
        assert response.json()["content"] == _payload()["content"]
        service.create_tailored_draft.assert_awaited_once()

    @pytest.mark.parametrize(
        "payload",
        [
            {"job_id": 11, "content": {"job_id": 11, "work_history": "invalid"}},
            _payload(job_id=11) | {"content": _content() | {"job_id": 12}},
        ],
    )
    def test_create_rejects_malformed_or_mismatched_content(
        self, payload: dict[str, object]
    ) -> None:
        service = AsyncMock(spec=ResumeService)
        jobs = AsyncMock()
        response = TestClient(_build_route_app(service=service, job_repository=jobs)).post(
            "/resume/drafts", json=payload
        )

        assert response.status_code == 422
        service.create_tailored_draft.assert_not_awaited()

    def test_list_is_user_scoped_newest_first_and_metadata_only(self) -> None:
        service = AsyncMock(spec=ResumeService)
        service.list_tailored_drafts.return_value = [
            _list_item(draft_id=2, title="New role"),
            _list_item(draft_id=1, title="Old role"),
        ]
        response = TestClient(_build_route_app(service=service, job_repository=AsyncMock())).get(
            "/resume/drafts"
        )

        assert response.status_code == 200
        data = response.json()
        assert [item["id"] for item in data] == [2, 1]
        assert all("content" not in item for item in data)
        service.list_tailored_drafts.assert_awaited_once_with(user_id=7)

    def test_owner_detail_delete_and_cross_user_access(self) -> None:
        service = AsyncMock(spec=ResumeService)
        service.get_tailored_draft.return_value = _response()
        service.delete_tailored_draft.return_value = None
        client = TestClient(_build_route_app(service=service, job_repository=AsyncMock()))

        detail = client.get("/resume/drafts/4")
        assert detail.status_code == 200
        assert detail.json()["content"]["job_id"] == 11
        assert client.delete("/resume/drafts/4").status_code == 204

        service.get_tailored_draft.side_effect = TailoredResumeDraftNotFoundError(4)
        service.delete_tailored_draft.side_effect = TailoredResumeDraftNotFoundError(4)
        assert client.get("/resume/drafts/4").status_code == 404
        assert client.delete("/resume/drafts/4").status_code == 404
        assert service.get_tailored_draft.await_args.kwargs["user_id"] == 7
        assert service.delete_tailored_draft.await_args.kwargs["user_id"] == 7

    def test_draft_crud_route_does_not_call_tailoring(self) -> None:
        service = AsyncMock(spec=ResumeService)
        service.list_tailored_drafts.return_value = []
        service.create_tailored_draft.return_value = _response()
        client = TestClient(_build_route_app(service=service, job_repository=AsyncMock()))

        assert client.get("/resume/drafts").status_code == 200
        assert client.post("/resume/drafts", json=_payload()).status_code == 201
        assert not service.tailor_for_job.called
