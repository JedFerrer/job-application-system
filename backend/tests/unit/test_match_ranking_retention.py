"""Regression coverage for automatic ranking and retained manual analyses."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.domain.matching.repository import MatchingRepository
from app.domain.matching.service import MatchingService

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_vector_ranking_query_excludes_manual_match_rows() -> None:
    result = type("Result", (), {"all": lambda self: []})()
    session = AsyncMock()
    session.execute.return_value = result
    repository = MatchingRepository(session)

    await repository.rank_jobs_by_vector([0.1, 0.2], 200, user_id=7)

    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    bound_values = list(compiled.params.values())
    assert "user_job_matches.origin" in sql
    assert "user_job_matches.user_id" in sql
    assert "NOT IN" in sql
    assert "jobs.status =" in sql
    assert "active" in bound_values
    assert "jobs.description_embedding IS NOT NULL" in sql
    assert "jobs.visibility =" in sql
    assert "jobs.created_by_user_id" in sql
    assert "user_jobs.status =" in sql
    assert "archived" in bound_values


@pytest.mark.asyncio
async def test_pruning_query_only_targets_eligible_automatic_rows() -> None:
    session = AsyncMock()
    repository = MatchingRepository(session)

    await repository.prune_automatic_matches(user_id=7, limit=200)

    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    bound_values = list(compiled.params.values())
    assert "DELETE FROM user_job_matches" in sql
    assert "user_job_matches.origin =" in sql
    assert "automatic" in bound_values
    assert "user_job_matches.id IN" in sql
    assert "jobs.status =" in sql
    assert "active" in bound_values
    assert "jobs.description_embedding IS NOT NULL" in sql
    assert "user_jobs.status =" in sql
    assert "archived" in bound_values


@pytest.mark.asyncio
async def test_full_rank_has_200_automatic_candidates_and_refreshes_manual_rows() -> None:
    automatic_ids = list(range(1000, 1200))
    manual_ids = [10, 11]
    calls: list[dict[str, object]] = []

    class FakeRepository:
        async def get_active_profile(self, user_id: int) -> object:
            return SimpleNamespace(
                id=3,
                embedding=[0.1, 0.2],
                parsed={"skills": ["Python"], "work_history": []},
            )

        async def rank_jobs_by_vector(
            self, embedding: list[float], limit: int, *, user_id: int
        ) -> list[tuple[int, float, str]]:
            assert limit == 200
            return [(job_id, 0.7, "Python services") for job_id in automatic_ids]

        async def list_manual_match_job_ids(self, *, user_id: int) -> list[int]:
            return manual_ids

        async def score_visible_job(self, **kwargs: object) -> float:
            return 0.6

        async def upsert_match(self, **kwargs: object) -> None:
            calls.append(dict(kwargs))

        async def prune_automatic_matches(self, *, user_id: int, limit: int) -> None:
            assert limit == 200

    response = await MatchingService(FakeRepository()).rank_jobs(1, 200)  # type: ignore[arg-type]

    automatic_calls = [call for call in calls if call["job_id"] in automatic_ids]
    manual_refresh_calls = [call for call in calls if call["job_id"] in manual_ids]
    assert response.ranked == 200
    assert len(automatic_calls) == 200
    assert len(manual_refresh_calls) == len(manual_ids)
    assert automatic_calls[0]["matched_skills"] == ["python"]
    assert automatic_calls[0]["missing_keywords"] == []
    assert automatic_calls[0]["deterministic_match"] is not None


@pytest.mark.asyncio
async def test_automatic_rank_persists_required_gaps_only_from_classified_mentions() -> None:
    calls: list[dict[str, object]] = []

    class FakeRepository:
        async def get_active_profile(self, user_id: int) -> object:
            return SimpleNamespace(
                id=3,
                embedding=[0.1, 0.2],
                parsed={"skills": ["Python", "Docker", "GraphQL"]},
            )

        async def rank_jobs_by_vector(
            self, embedding: list[float], limit: int, *, user_id: int
        ) -> list[tuple[int, float, str]]:
            return [(101, 0.9, "ignored persisted evidence")]

        async def list_manual_match_job_ids(self, *, user_id: int) -> list[int]:
            return []

        async def upsert_match(self, **kwargs: object) -> None:  # noqa: ANN003
            calls.append(dict(kwargs))

        async def prune_automatic_matches(self, *, user_id: int, limit: int) -> None:
            return None

    class FakeMentionRepository:
        async def list_for_job(self, job_id: int):  # noqa: ANN001
            return [
                SimpleNamespace(canonical_label="python", classification="required"),
                SimpleNamespace(canonical_label="kubernetes", classification="required"),
                SimpleNamespace(canonical_label="docker", classification="preferred"),
                SimpleNamespace(canonical_label="graphql", classification="unclassified"),
            ]

    await MatchingService(
        FakeRepository(), mention_repository=FakeMentionRepository()  # type: ignore[arg-type]
    ).rank_jobs(1, 200)

    assert calls[0]["matched_skills"] == ["python", "docker", "graphql"]
    assert calls[0]["missing_keywords"] == ["kubernetes"]
    assert calls[0]["deterministic_match"]["missing_skills"] == ["kubernetes"]  # type: ignore[index]
