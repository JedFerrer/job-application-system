"""Unit coverage for deterministic hybrid candidate fusion."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.elements import ClauseElement

from app.domain.matching.hybrid_retrieval import (
    RetrievalCandidate,
    fuse_ranked_candidates,
)
from app.domain.matching.repository import MatchingRepository
from app.domain.matching.service import MatchingService

pytestmark = pytest.mark.unit


def test_rrf_deduplicates_and_preserves_source_metadata() -> None:
    fused = fuse_ranked_candidates(
        {
            "text": [RetrievalCandidate(2, 0.8), RetrievalCandidate(1, 0.7)],
            "vector": [RetrievalCandidate(1, 0.95), RetrievalCandidate(3, 0.6)],
            "skill": [RetrievalCandidate(1, 2.0)],
        },
        k=60,
    )

    assert [item.job_id for item in fused] == [1, 2, 3]
    assert fused[0].sources == ("skill", "text", "vector")
    assert fused[0].source_ranks == {"skill": 1, "text": 2, "vector": 1}
    assert fused[0].source_scores["vector"] == 0.95
    assert fused[0].metadata()["sources"] == ["skill", "text", "vector"]


def test_rrf_numeric_score_and_duplicate_source_entries() -> None:
    fused = fuse_ranked_candidates(
        {
            "vector": [
                RetrievalCandidate(7, 0.9),
                RetrievalCandidate(7, 0.9),
                RetrievalCandidate(8, 0.8),
            ],
            "skill": [RetrievalCandidate(7, 2.0)],
        },
        k=60,
    )

    item = next(candidate for candidate in fused if candidate.job_id == 7)
    assert item.rrf_score == pytest.approx((1 / 61) + (1 / 61))
    assert item.source_ranks == {"skill": 1, "vector": 1}


def test_rrf_ties_are_deterministic_and_limit_is_applied() -> None:
    fused = fuse_ranked_candidates(
        {
            "a": [RetrievalCandidate(9, 1.0)],
            "b": [RetrievalCandidate(4, 1.0)],
        },
        k=60,
        limit=1,
    )

    assert [item.job_id for item in fused] == [4]


def test_rrf_keeps_at_most_400_fused_candidates() -> None:
    fused = fuse_ranked_candidates(
        {"vector": [RetrievalCandidate(job_id, 1.0) for job_id in range(500)]},
        k=60,
        limit=400,
    )

    assert len(fused) == 400
    assert fused[-1].job_id == 399


def test_rrf_rejects_invalid_parameters() -> None:
    with pytest.raises(ValueError):
        fuse_ranked_candidates({}, k=0)
    with pytest.raises(ValueError):
        fuse_ranked_candidates({}, limit=-1)


def test_profile_retrieval_inputs_exclude_free_text_and_contact_fields() -> None:
    query, skill_ids = MatchingService._profile_retrieval_inputs(
        {
            "preferred_roles": ["Backend Engineer"],
            "headline": "Python developer",
            "skills": ["Python"],
            "certifications": ["AWS"],
            "email": "private@example.com",
            "phone": "+1 555 0100",
            "work_history": [{"company": "Private Employer", "highlights": ["Secret"]}],
        }
    )

    assert "Backend Engineer" in query
    assert "Python" in query
    assert "private@example.com" not in query
    assert "Private Employer" not in query
    assert skill_ids


def test_profile_retrieval_inputs_allow_empty_queries() -> None:
    assert MatchingService._profile_retrieval_inputs({}) == ("", set())


@pytest.mark.asyncio
async def test_hybrid_candidates_include_text_and_skill_only_results() -> None:
    class Repository:
        async def rank_jobs_by_vector(
            self, embedding: list[float], limit: int, *, user_id: int
        ) -> list[tuple[int, float, str]]:
            return [(10, 0.95, "vector job")]

        async def search_jobs_by_text(
            self, query: str, limit: int, *, user_id: int
        ) -> list[RetrievalCandidate]:
            return [RetrievalCandidate(20, 3.0)]

        async def search_jobs_by_skills(
            self, skill_ids: set[str], limit: int, *, user_id: int
        ) -> list[RetrievalCandidate]:
            return [RetrievalCandidate(30, 2.0)]

        async def vector_similarities_for_jobs(
            self, *, user_id: int, embedding: list[float], job_ids: list[int]
        ) -> dict[int, float]:
            return {20: 0.4, 30: 0.3}

    results = await MatchingService(cast(MatchingRepository, Repository()))._hybrid_candidates(
        user_id=7,
        embedding=[0.1, 0.2],
        profile_parsed={
            "preferred_roles": ["Backend Engineer"],
            "headline": "Python developer",
            "skills": ["Python"],
            "certifications": [],
        },
    )

    assert {item[0] for item in results} == {10, 20, 30}
    assert results[1][3]["sources"]


@pytest.mark.asyncio
async def test_hybrid_branch_failure_keeps_other_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Repository:
        async def rank_jobs_by_vector(
            self, embedding: list[float], limit: int, *, user_id: int
        ) -> list[tuple[int, float, str]]:
            return [(10, 0.95, "vector job")]

        async def search_jobs_by_text(
            self, query: str, limit: int, *, user_id: int
        ) -> list[RetrievalCandidate]:
            raise RuntimeError("simulated text failure")

        async def search_jobs_by_skills(
            self, skill_ids: set[str], limit: int, *, user_id: int
        ) -> list[RetrievalCandidate]:
            return [RetrievalCandidate(30, 2.0)]

        async def vector_similarities_for_jobs(
            self, *, user_id: int, embedding: list[float], job_ids: list[int]
        ) -> dict[int, float]:
            return {30: 0.3}

    warning = Mock()
    monkeypatch.setattr("app.domain.matching.service.logger.warning", warning)
    results = await MatchingService(cast(MatchingRepository, Repository()))._hybrid_candidates(
        user_id=7,
        embedding=[0.1, 0.2],
        profile_parsed={"headline": "Python", "skills": ["Python"]},
    )

    assert {item[0] for item in results} == {10, 30}
    warning.assert_called_once()
    warning_call = warning.call_args
    assert warning_call.args[0] == "matches.hybrid_retrieval_branch_failed"
    assert warning_call.kwargs["branch"] == "text"
    assert "Python" not in repr(warning_call)
    assert "simulated text failure" not in repr(warning_call)


@pytest.mark.asyncio
async def test_repository_hybrid_branches_share_eligibility_predicate() -> None:
    session = AsyncMock()
    session.execute.return_value = type("Result", (), {"all": lambda self: []})()
    repository = MatchingRepository(session)

    await repository.search_jobs_by_text("Backend Engineer", 400, user_id=7)
    text_statement = cast(ClauseElement, session.execute.await_args_list[-1].args[0])
    text_dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    text_compiled = text_statement.compile(dialect=text_dialect)
    text_sql = str(text_compiled)
    await repository.search_jobs_by_skills({"local:python"}, 400, user_id=7)
    skill_statement = cast(ClauseElement, session.execute.await_args_list[-1].args[0])
    skill_dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    skill_compiled = skill_statement.compile(dialect=skill_dialect)
    skill_sql = str(skill_compiled)

    for sql, params in (
        (text_sql, text_compiled.params),
        (skill_sql, skill_compiled.params),
    ):
        assert "jobs.status =" in sql
        assert "jobs.description_embedding IS NOT NULL" in sql
        assert "jobs.visibility =" in sql
        assert "jobs.created_by_user_id" in sql
        assert "user_jobs.status =" in sql
        assert "archived" in params.values()
        assert "user_job_matches.origin" in sql
    assert "jobs.search_vector" in text_sql
    assert "job_skill_mentions.skill_id" in skill_sql


@pytest.mark.asyncio
async def test_repository_hybrid_empty_inputs_do_not_query_database() -> None:
    session = AsyncMock()
    repository = MatchingRepository(session)

    assert await repository.search_jobs_by_text("   ", 400, user_id=7) == []
    assert await repository.search_jobs_by_skills(set(), 400, user_id=7) == []
    session.execute.assert_not_awaited()
