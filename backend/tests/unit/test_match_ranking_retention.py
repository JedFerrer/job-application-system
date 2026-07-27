"""Regression coverage for automatic ranking and retained manual analyses."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.domain.matching.hybrid_retrieval import RetrievalCandidate
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
async def test_fit_pruning_orders_by_ranking_score_and_nulls_last() -> None:
    session = AsyncMock()
    repository = MatchingRepository(session)

    await repository.prune_automatic_matches(user_id=7, limit=3, ranking_mode="fit")

    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "user_job_matches.ranking_score DESC NULLS LAST" in sql
    assert "user_job_matches.vector_score DESC" not in sql
    assert "user_job_matches.origin =" in sql
    assert "automatic" in compiled.params.values()


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
    assert isinstance(automatic_calls[0]["fit_score"], float)
    assert isinstance(automatic_calls[0]["ranking_score"], float)
    assert isinstance(automatic_calls[0]["score_components"], dict)
    assert automatic_calls[0]["source_profile_id"] == 3
    assert automatic_calls[0]["job_description_hash"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["vector", "shadow", "fit"])
async def test_ranking_modes_use_vector_placeholder_without_hybrid_only_candidates(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    automatic_ids = list(range(1000, 1250))
    manual_ids = [10]
    calls: list[dict[str, object]] = []

    class FakeRepository:
        async def get_active_profile(self, user_id: int) -> object:
            return SimpleNamespace(
                id=3,
                embedding=[0.1, 0.2],
                parsed={"skills": ["Python"]},
            )

        async def rank_jobs_by_vector(
            self, embedding: list[float], limit: int, *, user_id: int
        ) -> list[tuple[int, float, str]]:
            assert limit == 123
            return [(job_id, 0.7, "Python services") for job_id in automatic_ids[:limit]]

        async def search_jobs_by_text(
            self, query: str, limit: int, *, user_id: int
        ) -> list[RetrievalCandidate]:
            raise AssertionError("text retrieval must not run in vector mode")

        async def search_jobs_by_skills(
            self, skill_ids: set[str], limit: int, *, user_id: int
        ) -> list[RetrievalCandidate]:
            raise AssertionError("skill retrieval must not run in vector mode")

        async def vector_similarities_for_jobs(
            self, *, user_id: int, embedding: list[float], job_ids: list[int]
        ) -> dict[int, float]:
            raise AssertionError("vector enrichment must not run in vector mode")

        async def list_manual_match_job_ids(self, *, user_id: int) -> list[int]:
            return manual_ids

        async def score_visible_job(self, **kwargs: object) -> float:
            return 0.6

        async def upsert_match(self, **kwargs: object) -> None:
            calls.append(dict(kwargs))

        async def prune_automatic_matches(self, *, user_id: int, limit: int) -> None:
            assert limit == 123

    monkeypatch.setattr(
        "app.domain.matching.service.get_settings",
        lambda: Settings(match_ranking_mode=mode, match_final_limit=123),
    )

    response = await MatchingService(FakeRepository()).rank_jobs(1, 200)  # type: ignore[arg-type]

    assert response.ranked == 123
    assert len([call for call in calls if call["job_id"] in automatic_ids]) == 123
    assert {call["job_id"] for call in calls} == {*automatic_ids[:123], *manual_ids}
    assert len([call for call in calls if call["job_id"] in manual_ids]) == 1


@pytest.mark.asyncio
async def test_incremental_ranking_prunes_with_configured_final_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upserted: list[dict[str, object]] = []
    prune_limits: list[int] = []

    class FakeRepository:
        async def get_active_profile(self, user_id: int) -> object:
            return SimpleNamespace(
                id=3,
                embedding=[0.1, 0.2],
                parsed={"skills": ["Python"]},
            )

        async def score_visible_job(self, **kwargs: object) -> float:
            return 0.7

        async def get_visible_job(self, *, user_id: int, job_id: int) -> object:
            return SimpleNamespace(
                id=job_id,
                title="Python Engineer",
                description="Python services",
            )

        async def upsert_match(self, **kwargs: object) -> None:
            upserted.append(dict(kwargs))

        async def prune_automatic_matches(self, *, user_id: int, limit: int) -> None:
            prune_limits.append(limit)

    monkeypatch.setattr(
        "app.domain.matching.service.get_settings",
        lambda: Settings(match_ranking_mode="shadow", match_final_limit=17),
    )

    ranked = await MatchingService(FakeRepository()).rank_job_ids(  # type: ignore[arg-type]
        user_id=1,
        job_ids=[101, 102],
        limit=200,
    )

    assert ranked == 2
    assert [call["job_id"] for call in upserted] == [101, 102]
    assert all(call["origin"] == "automatic" for call in upserted)
    assert prune_limits == [17]


@pytest.mark.asyncio
async def test_shadow_scores_hybrid_candidates_but_persists_only_vector_intersection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upserted: list[dict[str, object]] = []
    prune_limits: list[int] = []
    log_info = Mock()

    class FakeRepository:
        async def get_active_profile(self, user_id: int) -> object:
            return SimpleNamespace(
                id=3,
                embedding=[0.1, 0.2],
                parsed={
                    "preferred_roles": ["Backend Engineer"],
                    "headline": "Python developer",
                    "skills": ["Python"],
                },
            )

        async def rank_jobs_by_vector(
            self, embedding: list[float], limit: int, *, user_id: int
        ) -> list[tuple[int, float, str]]:
            if limit == 3:
                return [
                    (1, 0.99, "vector one"),
                    (2, 0.90, "vector two"),
                    (5, 0.80, "vector five"),
                ]
            return [
                (1, 0.99, "vector one"),
                (4, 0.97, "shadow only"),
                (2, 0.90, "vector two"),
                (3, 0.80, "hybrid only three"),
            ]

        async def search_jobs_by_text(
            self, query: str, limit: int, *, user_id: int
        ) -> list[RetrievalCandidate]:
            return [RetrievalCandidate(4, 3.0), RetrievalCandidate(2, 2.0)]

        async def search_jobs_by_skills(
            self, skill_ids: set[str], limit: int, *, user_id: int
        ) -> list[RetrievalCandidate]:
            return [RetrievalCandidate(2, 2.0), RetrievalCandidate(3, 1.0)]

        async def get_visible_jobs_by_ids(
            self, *, user_id: int, job_ids: list[int]
        ) -> dict[int, object]:
            return {
                job_id: SimpleNamespace(
                    id=job_id,
                    title="Backend Engineer",
                    description=f"Description {job_id}",
                )
                for job_id in job_ids
            }

        async def list_manual_match_job_ids(self, *, user_id: int) -> list[int]:
            return []

        async def upsert_match(self, **kwargs: object) -> None:
            upserted.append(dict(kwargs))

        async def prune_automatic_matches(self, *, user_id: int, limit: int) -> None:
            prune_limits.append(limit)

    monkeypatch.setattr(
        "app.domain.matching.service.get_settings",
        lambda: Settings(
            match_ranking_mode="shadow",
            match_vector_candidate_limit=4,
            match_text_candidate_limit=4,
            match_skill_candidate_limit=4,
            match_final_limit=3,
            match_rrf_k=60,
        ),
    )
    monkeypatch.setattr("app.domain.matching.service.logger.info", log_info)

    response = await MatchingService(FakeRepository()).rank_jobs(1, 200)  # type: ignore[arg-type]

    assert response.ranked == 3
    assert [call["job_id"] for call in upserted] == [1, 2, 5]
    assert all(call["job_id"] != 4 for call in upserted)
    first_metadata = cast(dict[str, object], upserted[0]["retrieval_metadata"])
    second_metadata = cast(dict[str, object], upserted[1]["retrieval_metadata"])
    assert set(cast(list[str], first_metadata["sources"])) == {"vector"}
    assert set(cast(list[str], second_metadata["sources"])) == {"skill", "text", "vector"}
    assert upserted[2]["retrieval_metadata"] == {}
    assert prune_limits == [3, 3]

    shadow_call = next(
        call for call in log_info.call_args_list if call.args[0] == "matches.shadow_metrics"
    )
    assert sum(call.args[0] == "matches.shadow_metrics" for call in log_info.call_args_list) == 1
    metrics = shadow_call.kwargs
    assert metrics["vector_result_count"] == 3
    assert metrics["hybrid_candidate_count"] == 4
    assert metrics["intersection_count"] == 2
    assert metrics["shadow_only_count"] == 2
    assert metrics["top_10_overlap"] == 2
    assert metrics["top_50_overlap"] == 2
    assert metrics["retrieval_contribution_counts"] == {"skill": 2, "text": 2, "vector": 4}
    assert metrics["mean_rank_movement"] == pytest.approx(0.5)
    assert isinstance(metrics["scoring_duration_ms"], float)
    assert "Description" not in repr(shadow_call)
    assert "Backend Engineer" not in repr(shadow_call)


@pytest.mark.asyncio
async def test_shadow_retrieval_failure_keeps_vector_visible_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upserted: list[dict[str, object]] = []
    log_info = Mock()
    log_warning = Mock()

    class FakeRepository:
        async def get_active_profile(self, user_id: int) -> object:
            return SimpleNamespace(
                id=3,
                embedding=[0.1, 0.2],
                parsed={
                    "preferred_roles": ["Backend Engineer"],
                    "headline": "Python developer",
                    "skills": ["Python"],
                },
            )

        async def rank_jobs_by_vector(
            self, embedding: list[float], limit: int, *, user_id: int
        ) -> list[tuple[int, float, str]]:
            return [(1, 0.9, "vector one"), (2, 0.8, "vector two")]

        async def search_jobs_by_text(
            self, query: str, limit: int, *, user_id: int
        ) -> list[RetrievalCandidate]:
            raise RuntimeError("Python developer raw query and job description secret")

        async def search_jobs_by_skills(
            self, skill_ids: set[str], limit: int, *, user_id: int
        ) -> list[RetrievalCandidate]:
            raise RuntimeError("profile skill label secret")

        async def list_manual_match_job_ids(self, *, user_id: int) -> list[int]:
            return []

        async def upsert_match(self, **kwargs: object) -> None:
            upserted.append(dict(kwargs))

        async def prune_automatic_matches(self, *, user_id: int, limit: int) -> None:
            return None

    monkeypatch.setattr(
        "app.domain.matching.service.get_settings",
        lambda: Settings(match_ranking_mode="shadow", match_final_limit=2),
    )
    monkeypatch.setattr("app.domain.matching.service.logger.info", log_info)
    monkeypatch.setattr("app.domain.matching.service.logger.warning", log_warning)

    response = await MatchingService(FakeRepository()).rank_jobs(1, 200)  # type: ignore[arg-type]

    assert response.ranked == 2
    assert [call["job_id"] for call in upserted] == [1, 2]
    assert len(log_warning.call_args_list) == 2
    for warning_call in log_warning.call_args_list:
        assert warning_call.kwargs["error_type"] == "RuntimeError"
        assert "exc_info" not in warning_call.kwargs
        warning_repr = repr(warning_call)
        assert "Python developer" not in warning_repr
        assert "raw query" not in warning_repr
        assert "job description secret" not in warning_repr
        assert "profile skill label secret" not in warning_repr
    shadow_call = next(
        call for call in log_info.call_args_list if call.args[0] == "matches.shadow_metrics"
    )
    assert sum(call.args[0] == "matches.shadow_metrics" for call in log_info.call_args_list) == 1
    assert shadow_call.kwargs["hybrid_candidate_count"] == 2


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
        FakeRepository(),  # type: ignore[arg-type]
        mention_repository=FakeMentionRepository(),  # type: ignore[arg-type]
    ).rank_jobs(1, 200)

    assert calls[0]["matched_skills"] == ["python", "docker", "graphql"]
    assert calls[0]["missing_keywords"] == ["kubernetes"]
    assert calls[0]["deterministic_match"]["missing_skills"] == ["kubernetes"]  # type: ignore[index]
