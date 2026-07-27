"""Unit tests for the Phase 5C1 synthetic ranking evaluator."""

from __future__ import annotations

import math
from unittest.mock import Mock

import pytest

import app.domain.matching.hybrid_retrieval as hybrid_module
import app.domain.matching.scorer as scorer_module
from app.domain.matching.scorer import DeterministicScoreInput
from tests.matching_evaluation import (
    SyntheticQuery,
    hybrid_ranking,
    ndcg_at_10,
    prospective_fit_ranking,
    recall_at_50,
    vector_ranking,
)

pytestmark = pytest.mark.unit


def _candidate(
    candidate_id: int,
    grade: int,
    vector_score: float,
    *,
    vector_rank: int | None = None,
    text_rank: int | None = None,
    skill_rank: int | None = None,
) -> dict[str, object]:
    retrieval: dict[str, dict[str, object]] = {
        "vector": {
            "rank": vector_rank if vector_rank is not None else candidate_id,
            "score": vector_score,
        }
    }
    if text_rank is not None:
        retrieval["text"] = {"rank": text_rank, "score": 1.0 / text_rank}
    if skill_rank is not None:
        retrieval["skill"] = {"rank": skill_rank, "score": 1.0 / skill_rank}
    return {
        "candidate_id": candidate_id,
        "relevance_grade": grade,
        "retrieval": retrieval,
        "score_input": DeterministicScoreInput(),
    }


def _query(*candidates: dict[str, object], rrf_k: int = 60) -> SyntheticQuery:
    return SyntheticQuery.from_mapping(
        {"query_id": "synthetic-inline-01", "candidates": list(candidates), "rrf_k": rrf_k}
    )


def test_ndcg_at_10_matches_the_graded_definition() -> None:
    query = _query(
        _candidate(1, 3, 0.9),
        _candidate(2, 0, 0.8),
        _candidate(3, 2, 0.7),
    )

    value = ndcg_at_10(vector_ranking(query))
    expected = (7 + 3 / math.log2(4)) / (7 + 3 / math.log2(3))

    assert value == pytest.approx(expected)


def test_recall_at_50_counts_grade_two_and_three() -> None:
    candidates = [_candidate(1, 3, 1.0)]
    candidates.extend(_candidate(index, 0, 1.0 - index / 100) for index in range(2, 51))
    candidates.append(_candidate(51, 2, 0.1))

    assert recall_at_50(vector_ranking(_query(*candidates))) == pytest.approx(0.5)


def test_recall_at_50_is_one_when_no_candidate_is_relevant() -> None:
    query = _query(_candidate(1, 0, 0.9), _candidate(2, 1, 0.8))

    assert recall_at_50(vector_ranking(query)) == 1.0


def test_ndcg_at_10_is_zero_for_an_all_zero_relevance_set() -> None:
    query = _query(_candidate(1, 0, 0.9), _candidate(2, 0, 0.8))

    assert ndcg_at_10(vector_ranking(query)) == 0.0


def test_vector_ties_are_sorted_by_ascending_candidate_id() -> None:
    query = _query(
        _candidate(9, 1, 0.8, vector_rank=2),
        _candidate(2, 1, 0.8, vector_rank=1),
    )

    assert [item.candidate_id for item in vector_ranking(query)] == [2, 9]


def test_prospective_fit_ranking_is_deterministic_and_test_only() -> None:
    query = _query(
        _candidate(9, 1, 0.8, vector_rank=2),
        _candidate(2, 1, 0.8, vector_rank=1),
    )

    assert [item.candidate_id for item in prospective_fit_ranking(query)] == [2, 9]


def test_prospective_fit_ranking_uses_production_scorer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = _query(_candidate(1, 2, 0.8))
    production_scorer = scorer_module.score_match
    spy = Mock(wraps=production_scorer)
    monkeypatch.setattr(scorer_module, "score_match", spy)

    prospective_fit_ranking(query)

    assert spy.call_count == 1


def test_rrf_ranking_uses_production_fusion_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = _query(
        _candidate(1, 3, 0.9, vector_rank=1, text_rank=2),
        _candidate(2, 2, 0.8, vector_rank=2, text_rank=1),
        rrf_k=1,
    )

    production_fuser = hybrid_module.fuse_ranked_candidates
    spy = Mock(wraps=production_fuser)
    monkeypatch.setattr(hybrid_module, "fuse_ranked_candidates", spy)

    assert [item.candidate_id for item in hybrid_ranking(query)] == [1, 2]
    assert spy.call_count == 1


def test_invalid_relevance_grade_is_rejected() -> None:
    with pytest.raises(ValueError, match="relevance_grade"):
        _query(_candidate(1, 4, 0.9))


def test_duplicate_candidate_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="candidate IDs"):
        _query(_candidate(1, 2, 0.9), _candidate(1, 1, 0.8, vector_rank=2))


def test_malformed_retrieval_rank_is_rejected() -> None:
    candidate = _candidate(1, 2, 0.9)
    retrieval = candidate["retrieval"]
    assert isinstance(retrieval, dict)
    retrieval["vector"] = {"rank": 0, "score": 0.9}

    with pytest.raises(ValueError, match="rank must be positive"):
        _query(candidate)


def test_contradictory_vector_score_and_rank_are_rejected() -> None:
    first = _candidate(1, 2, 0.9, vector_rank=2)
    second = _candidate(2, 2, 0.8, vector_rank=1)

    with pytest.raises(ValueError, match="vector_rank must match"):
        _query(first, second)


def test_missing_expected_field_is_rejected() -> None:
    candidate = _candidate(1, 2, 0.9)
    del candidate["score_input"]

    with pytest.raises(ValueError, match="score_input"):
        _query(candidate)


def test_malformed_optional_score_input_string_is_rejected() -> None:
    candidate = _candidate(1, 2, 0.9)
    candidate["score_input"] = {"job_title": 123}

    with pytest.raises(ValueError, match="job_title must be a string"):
        _query(candidate)
