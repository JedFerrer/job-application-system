"""Unit coverage for the dashboard search-readiness aggregate."""

from __future__ import annotations

import pytest

from app.domain.dashboard.search_readiness import SearchReadinessService
from app.domain.preferences.repository import PreferenceRepository

pytestmark = pytest.mark.unit


class FakeResumeRepository:
    def __init__(self, snapshot: tuple[bool, bool, bool]) -> None:
        self.snapshot = snapshot

    async def get_search_readiness_snapshot(self, user_id: int) -> tuple[bool, bool, bool]:
        assert user_id == 7
        return self.snapshot


class FakePreferenceRepository:
    def __init__(self, configured: bool) -> None:
        self.configured = configured

    async def has_configured_search_titles(self, user_id: int) -> bool:
        assert user_id == 7
        return self.configured


def service(snapshot: tuple[bool, bool, bool], configured: bool) -> SearchReadinessService:
    return SearchReadinessService(
        FakeResumeRepository(snapshot), FakePreferenceRepository(configured)  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("snapshot", "configured", "overall", "reason"),
    [
        ((False, False, False), False, "needs_setup", "import_resume"),
        ((False, False, True), False, "needs_setup", "draft_review"),
        ((True, True, False), False, "almost_ready", "set_job_preferences"),
        ((True, True, True), True, "ready", "view_matches"),
    ],
)
async def test_states_and_priority(
    snapshot: tuple[bool, bool, bool],
    configured: bool,
    overall: str,
    reason: str,
) -> None:
    response = await service(snapshot, configured).get(7)

    assert response.overall_state == overall
    assert response.next_action.reason == reason
    assert response.next_action.label in {
        "Import resume",
        "Review resume draft",
        "Set job preferences",
        "View matches",
    }


@pytest.mark.asyncio
async def test_active_profile_with_pending_draft_is_ready_and_draft_is_non_blocking() -> None:
    response = await service((True, True, True), True).get(7)

    assert response.overall_state == "ready"
    assert response.checks.resume.state == "ready"
    assert response.checks.resume.has_pending_draft is True
    assert response.next_action.reason == "view_matches"


@pytest.mark.asyncio
async def test_active_resume_without_embedding_needs_profile_completion() -> None:
    response = await service((True, False, True), True).get(7)

    assert response.overall_state == "almost_ready"
    assert response.checks.resume.state == "ready"
    assert response.checks.profile.state == "needs_setup"
    assert response.next_action.reason == "draft_review"


@pytest.mark.asyncio
async def test_multiple_unresolved_conditions_keep_draft_priority() -> None:
    response = await service((False, False, True), False).get(7)

    assert response.overall_state == "needs_setup"
    assert response.checks.resume.state == "draft_review"
    assert response.checks.preferences.state == "needs_setup"
    assert response.next_action.reason == "draft_review"


@pytest.mark.asyncio
async def test_response_is_compact_and_contains_no_private_fields() -> None:
    response = await service((True, True, True), True).get(7)
    payload = response.model_dump()

    assert payload == {
        "overall_state": "ready",
        "checks": {
                "resume": {"state": "ready", "has_pending_draft": True},
            "profile": {"state": "ready"},
            "preferences": {"state": "ready"},
        },
        "next_action": {"reason": "view_matches", "label": "View matches"},
    }
    assert not {"resume_id", "draft_id", "filename", "profile", "job_titles"}.intersection(
        payload
    )


class _PreferenceResult:
    def __init__(self, row: tuple[bool, list[str]] | None) -> None:
        self._row = row

    def one_or_none(self) -> tuple[bool, list[str]] | None:
        return self._row


class _PreferenceSession:
    def __init__(self, row: tuple[bool, list[str]] | None) -> None:
        self.row = row

    async def execute(self, _statement: object) -> _PreferenceResult:
        return _PreferenceResult(self.row)


@pytest.mark.asyncio
async def test_preference_summary_requires_a_nonblank_title() -> None:
    empty = PreferenceRepository(_PreferenceSession((True, [" ", "\t"])))  # type: ignore[arg-type]
    configured = PreferenceRepository(_PreferenceSession((True, [" ", "Backend Engineer"])))  # type: ignore[arg-type]

    assert await empty.has_configured_search_titles(7) is False
    assert await configured.has_configured_search_titles(7) is True
