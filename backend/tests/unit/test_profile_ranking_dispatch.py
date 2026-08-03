"""Profile edits commit before their coalesced ranking task is dispatched."""

from __future__ import annotations

import pytest
from fastapi import BackgroundTasks

from app.api.v1.resume import approve_resume_import, update_profile
from app.domain.resume.schemas import ParsedProfile, ProfileResponse, ProfileUpdate
from app.models.user import User


class FakeDb:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def commit(self) -> None:
        self.events.append("commit")


class FakeResumeService:
    def __init__(self, changed: bool) -> None:
        self.changed = changed

    async def update_profile(self, *, user_id: int, profile: ParsedProfile) -> ProfileResponse:
        del user_id
        return ProfileResponse(
            profile_id=1,
            resume_id=1,
            profile=profile,
            matching_profile_changed=self.changed,
        )


class FakeImportApprovalService:
    def __init__(self, changed: bool = True) -> None:
        self.changed = changed

    async def approve_import_draft(
        self, *, user_id: int, resume_id: int
    ) -> ProfileResponse:
        del user_id, resume_id
        return ProfileResponse(
            profile_id=1,
            resume_id=1,
            profile=ParsedProfile(headline="Backend engineer"),
            matching_profile_changed=self.changed,
        )


@pytest.mark.asyncio
async def test_changed_profile_commits_before_scheduling_ranking() -> None:
    events: list[str] = []
    background_tasks = BackgroundTasks()
    response = await update_profile(
        payload=ProfileUpdate(profile=ParsedProfile(headline="Backend engineer")),
        current_user=User(id=7, email="test@example.com", name="Test", is_active=True),
        service=FakeResumeService(changed=True),  # type: ignore[arg-type]
        db=FakeDb(events),  # type: ignore[arg-type]
        background_tasks=background_tasks,
    )

    assert response.matching_profile_changed is True
    assert events == ["commit"]
    assert len(background_tasks.tasks) == 1


@pytest.mark.asyncio
async def test_unchanged_profile_does_not_schedule_ranking() -> None:
    events: list[str] = []
    background_tasks = BackgroundTasks()
    await update_profile(
        payload=ProfileUpdate(profile=ParsedProfile()),
        current_user=User(id=7, email="test@example.com", name="Test", is_active=True),
        service=FakeResumeService(changed=False),  # type: ignore[arg-type]
        db=FakeDb(events),  # type: ignore[arg-type]
        background_tasks=background_tasks,
    )

    assert events == ["commit"]
    assert background_tasks.tasks == []


@pytest.mark.asyncio
async def test_approved_import_commits_before_scheduling_ranking() -> None:
    events: list[str] = []
    background_tasks = BackgroundTasks()
    response = await approve_resume_import(
        resume_id=9,
        current_user=User(id=7, email="test@example.com", name="Test", is_active=True),
        service=FakeImportApprovalService(),  # type: ignore[arg-type]
        db=FakeDb(events),  # type: ignore[arg-type]
        background_tasks=background_tasks,
    )

    assert response.matching_profile_changed is True
    assert events == ["commit"]
    assert len(background_tasks.tasks) == 1


@pytest.mark.asyncio
async def test_unchanged_approved_import_commits_without_scheduling_ranking() -> None:
    events: list[str] = []
    background_tasks = BackgroundTasks()
    response = await approve_resume_import(
        resume_id=9,
        current_user=User(id=7, email="test@example.com", name="Test", is_active=True),
        service=FakeImportApprovalService(changed=False),  # type: ignore[arg-type]
        db=FakeDb(events),  # type: ignore[arg-type]
        background_tasks=background_tasks,
    )

    assert response.matching_profile_changed is False
    assert events == ["commit"]
    assert background_tasks.tasks == []
