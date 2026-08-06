"""Focused unit coverage for dashboard match recommendations."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.matching.service import MatchingService

pytestmark = pytest.mark.unit


class _Repository:
    def __init__(self, candidates: list[tuple[object, object, str | None]]) -> None:
        self.candidates = candidates

    async def list_recommendation_candidates(self, *, user_id: int, limit: int):
        assert user_id == 7
        assert limit == 100
        return self.candidates


def _job(job_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=job_id,
        title=f"Role {job_id}",
        company="Example",
        location="Remote",
        is_remote=True,
        source="test",
        source_url=f"https://example.test/{job_id}",
        status="active",
        visibility="public",
        created_by_user_id=None,
    )


def _match(
    score: float,
    *,
    matched: list[str] | None = None,
    missing: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        vector_score=score,
        confidence="strong" if score >= 80 else "moderate",
        llm_score=None,
        llm_confidence=None,
        matched_skills=matched,
        missing_keywords=missing,
    )


@pytest.mark.asyncio
async def test_recommendations_filter_skip_and_continue_in_rank_order() -> None:
    service = MatchingService(
        _Repository(
            [
                (_job(3), _match(90), None),
                (_job(2), _match(40), None),
                (_job(1), _match(70), "saved"),
            ]
        )
    )

    response = await service.list_recommendations(user_id=7, limit=4)

    assert [item.job_id for item in response.items] == [3, 1]
    assert response.items[1].application_status == "saved"


@pytest.mark.asyncio
async def test_recommendations_cap_skill_evidence_and_project_missing_skill() -> None:
    service = MatchingService(
        _Repository(
            [
                (
                    _job(9),
                    _match(
                        90,
                        matched=["Python", "FastAPI", "Postgres", "Docker"],
                        missing=["Kubernetes", "Terraform"],
                    ),
                    None,
                )
            ]
        )
    )

    response = await service.list_recommendations(user_id=7, limit=4)

    assert response.items[0].matched_skills == ["Python", "FastAPI", "Postgres"]
    assert response.items[0].missing_skill == "Kubernetes"


@pytest.mark.asyncio
async def test_recommendations_exclude_rejected_and_closed_applications() -> None:
    service = MatchingService(
        _Repository(
            [
                (_job(3), _match(90), "rejected"),
                (_job(2), _match(85), "closed"),
                (_job(1), _match(80), "applied"),
            ]
        )
    )

    response = await service.list_recommendations(user_id=7, limit=4)

    assert [item.job_id for item in response.items] == [1]
    assert response.items[0].application_status == "applied"
