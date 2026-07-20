"""Unit coverage for fresh, non-persistent match evidence."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.matching.service import MatchingService

pytestmark = pytest.mark.unit


def _service(repo: object) -> MatchingService:
    service = MatchingService.__new__(MatchingService)
    service._repo = repo  # type: ignore[assignment]
    service._readiness_repo = None
    return service


@pytest.mark.asyncio
async def test_evidence_is_fresh_and_does_not_read_or_write_match_cache() -> None:
    job = SimpleNamespace(
        description=(
            "Requirements:\n- Python and Kubernetes\n"
            "Preferred:\n- Docker\n"
            "5+ years of software engineering experience\n"
            "5 years of Python experience"
        )
    )
    profile = SimpleNamespace(
        parsed={"skills": ["Python", "Docker"], "years_experience": 3}
    )

    class FakeRepo:
        async def get_visible_job(self, *, user_id: int, job_id: int) -> object:  # noqa: ANN001
            assert (user_id, job_id) == (9, 12)
            return job

        async def get_active_profile(self, user_id: int) -> object:  # noqa: ANN001
            assert user_id == 9
            return profile

    result = await _service(FakeRepo()).get_match_evidence(user_id=9, job_id=12)

    assert [item.skill for item in result.skills.required.matched] == ["python"]
    assert [item.skill for item in result.skills.required.missing] == ["kubernetes"]
    assert [item.skill for item in result.skills.preferred.matched] == ["docker"]
    assert [item.assessment for item in result.experience_requirements] == [
        "may_not_meet",
        "needs_review",
    ]
    assert result.experience_requirements[1].profile_years_experience is None


@pytest.mark.asyncio
async def test_evidence_needs_review_when_total_profile_years_are_missing() -> None:
    class FakeRepo:
        async def get_visible_job(self, **kwargs: object) -> object:  # noqa: ANN001
            return SimpleNamespace(description="Required: 5 years of experience")

        async def get_active_profile(self, user_id: int) -> object:  # noqa: ANN001
            return SimpleNamespace(parsed={"skills": []})

    result = await _service(FakeRepo()).get_match_evidence(user_id=1, job_id=2)

    assert result.experience_requirements[0].assessment == "needs_review"
