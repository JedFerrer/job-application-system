"""Unit tests for the Phase 5C1 synthetic ranking evaluator."""

from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import fmean
from typing import cast
from unittest.mock import Mock

import pytest

import app.domain.matching.hybrid_retrieval as hybrid_module
import app.domain.matching.scorer as scorer_module
from app.domain.matching.scorer import DeterministicScoreInput
from app.domain.matching.skill_matcher import (
    compute_skill_match,
    normalize_skill,
    profile_skill_ids,
)
from tests.matching_evaluation import (
    SyntheticQuery,
    hybrid_ranking,
    ndcg_at_10,
    prospective_fit_ranking,
    recall_at_50,
    vector_ranking,
)

pytestmark = pytest.mark.unit

_FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "matching_quality_v1.json"
_REQUIRED_DOMAINS = {
    "software",
    "healthcare",
    "finance",
    "project management",
    "sales",
    "general operations",
}


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


def _fixture() -> dict[str, object]:
    return cast(dict[str, object], json.loads(_FIXTURE_PATH.read_text(encoding="utf-8")))


def _skill_ids(values: object) -> set[str]:
    assert isinstance(values, list)
    return {
        skill["skill_id"]
        for skill in values
        if isinstance(skill, dict) and isinstance(skill.get("skill_id"), str)
    }


def _fixture_query(raw_query: dict[str, object]) -> SyntheticQuery:
    profile = cast(dict[str, object], raw_query["profile"])
    profile_skill_ids = set(cast(list[str], profile["skill_ids"]))
    candidates: list[dict[str, object]] = []
    for raw_candidate in cast(list[dict[str, object]], raw_query["candidates"]):
        required = cast(list[dict[str, str]], raw_candidate["required_skills"])
        preferred = cast(list[dict[str, str]], raw_candidate["preferred_skills"])
        candidates.append(
            {
                "candidate_id": raw_candidate["id"],
                "relevance_grade": raw_candidate["relevance_grade"],
                "retrieval": raw_candidate["retrieval"],
                "score_input": {
                    "required_skills": required,
                    "matched_required_skills": [
                        skill for skill in required if skill["skill_id"] in profile_skill_ids
                    ],
                    "preferred_skills": preferred,
                    "matched_preferred_skills": [
                        skill for skill in preferred if skill["skill_id"] in profile_skill_ids
                    ],
                    "job_title": raw_candidate["title"],
                    "readiness": {"blockers": raw_candidate["expected_blockers"]},
                },
            }
        )
    return SyntheticQuery.from_mapping(
        {"query_id": raw_query["id"], "candidates": candidates, "rrf_k": 60}
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


def test_synthetic_quality_fixture_has_required_schema_coverage_and_privacy_markers() -> None:
    fixture = _fixture()
    assert fixture["schema_version"] == "matching-quality-v1"
    assert fixture["scorer_version"] == "fit-scorer-v1"
    assert fixture["rrf_k"] == 60
    assert fixture["synthetic"] is True
    assert fixture["non_personal"] is True
    notes = cast(dict[str, str], fixture["evaluation_notes"])
    assert "non-discriminating" in notes["recall_at_50"]
    assert "10 candidates" in notes["recall_at_50"]
    queries = cast(list[dict[str, object]], fixture["queries"])
    assert {query["domain"] for query in queries} == _REQUIRED_DOMAINS
    assert all(query["synthetic"] is True and query["non_personal"] is True for query in queries)
    assert all(len(cast(list[object], query["candidates"])) >= 8 for query in queries)

    query_ids = [query["id"] for query in queries]
    candidate_ids = [
        candidate["id"]
        for query in queries
        for candidate in cast(list[dict[str, object]], query["candidates"])
    ]
    assert len(query_ids) == len(set(query_ids))
    assert len(candidate_ids) == len(set(candidate_ids))
    assert len(candidate_ids) >= 50

    required_fields = {
        "id", "title", "relevance_grade", "required_skills", "preferred_skills",
        "expected_blockers", "expected_matched_required_skill_ids",
        "expected_missing_required_skill_ids", "retrieval", "evidence_excerpts",
    }
    for query in queries:
        profile = cast(dict[str, object], query["profile"])
        matcher_inputs = cast(dict[str, object], query["matcher_inputs"])
        aliases = cast(dict[str, str], matcher_inputs["skill_aliases"])
        negative_cases = cast(list[dict[str, str | int]], query["negative_cases"])
        assert profile["synthetic"] is True and profile["non_personal"] is True
        assert set(aliases) == {
            skill_id
            for candidate in cast(list[dict[str, object]], query["candidates"])
            for skill_id in _skill_ids(
                cast(list[dict[str, str]], candidate["required_skills"])
                + cast(list[dict[str, str]], candidate["preferred_skills"])
            )
        }
        assert matcher_inputs["profile_skills"]
        assert isinstance(matcher_inputs["ambiguous_or_unrelated_phrase"], str)
        assert {case["type"] for case in negative_cases} == {
            "missing_required",
            "preferred_only_not_required",
            "ambiguous_or_unrelated_not_required",
        }
        identifiers = {
            candidate["id"] for candidate in cast(list[dict[str, object]], query["candidates"])
        }
        for order in cast(dict[str, list[int]], query["expected_order"]).values():
            assert set(order) == identifiers
        for candidate in cast(list[dict[str, object]], query["candidates"]):
            assert required_fields <= set(candidate)
            assert candidate["synthetic"] is True and candidate["non_personal"] is True
            assert candidate["evidence_excerpts"]


def test_synthetic_fixture_quality_gates_and_required_skill_false_positives() -> None:
    fixture = _fixture()
    queries = cast(list[dict[str, object]], fixture["queries"])
    alias_sets = [
        cast(dict[str, str], cast(dict[str, object], query["matcher_inputs"])["skill_aliases"])
        for query in queries
    ]
    assert len({tuple(aliases.values()) for aliases in alias_sets}) == len(_REQUIRED_DOMAINS)
    assert any(
        cast(dict[str, list[int]], query["expected_order"])["vector"]
        != cast(dict[str, list[int]], query["expected_order"])["hybrid"]
        for query in queries
    )
    assert any(
        cast(dict[str, list[int]], query["expected_order"])["vector"]
        != cast(dict[str, list[int]], query["expected_order"])["prospective_fit"]
        for query in queries
    )
    vector_ndcgs: list[float] = []
    hybrid_ndcgs: list[float] = []
    fit_ndcgs: list[float] = []
    vector_recalls: list[float] = []
    hybrid_recalls: list[float] = []
    fit_recalls: list[float] = []
    false_positives = 0

    for raw_query in queries:
        query = _fixture_query(raw_query)
        vector = vector_ranking(query)
        hybrid = hybrid_ranking(query)
        prospective_fit = prospective_fit_ranking(query)
        expected = cast(dict[str, list[int]], raw_query["expected_order"])
        assert [item.candidate_id for item in vector] == expected["vector"]
        assert [item.candidate_id for item in hybrid] == expected["hybrid"]
        assert [item.candidate_id for item in prospective_fit] == expected["prospective_fit"]
        vector_ndcgs.append(ndcg_at_10(vector))
        hybrid_ndcgs.append(ndcg_at_10(hybrid))
        fit_ndcgs.append(ndcg_at_10(prospective_fit))
        vector_recalls.append(recall_at_50(vector))
        hybrid_recalls.append(recall_at_50(hybrid))
        fit_recalls.append(recall_at_50(prospective_fit))

        profile = cast(dict[str, object], raw_query["profile"])
        matcher_inputs = cast(dict[str, object], raw_query["matcher_inputs"])
        aliases = cast(dict[str, str], matcher_inputs["skill_aliases"])
        profile_ids = cast(list[str], profile["skill_ids"])
        parsed_profile: dict[str, object] = {
            "skills": cast(list[str], matcher_inputs["profile_skills"]),
            "headline": matcher_inputs["ambiguous_or_unrelated_phrase"],
        }
        extracted_profile_ids = profile_skill_ids(parsed_profile)
        profile_skill_ids_for_fixture = {
            skill_id
            for skill_id in profile_ids
            if profile_skill_ids({"skills": [aliases[skill_id]]}) & extracted_profile_ids
        }
        for candidate in cast(list[dict[str, object]], raw_query["candidates"]):
            required_ids = _skill_ids(candidate["required_skills"])
            required_labels = [aliases[skill_id] for skill_id in required_ids]
            report = compute_skill_match(required_labels, parsed_profile)
            matched_labels = set(report.matched_skills)
            actual_matched = {
                skill_id
                for skill_id in required_ids
                if normalize_skill(aliases[skill_id]) in matched_labels
            }
            actual_missing = required_ids - actual_matched
            expected_matched = set(
                cast(list[str], candidate["expected_matched_required_skill_ids"])
            )
            expected_missing = set(
                cast(list[str], candidate["expected_missing_required_skill_ids"])
            )
            assert actual_matched == expected_matched
            assert actual_missing == expected_missing
            preferred_ids = _skill_ids(candidate["preferred_skills"])
            assert preferred_ids.isdisjoint(actual_matched)
            false_positives += len(actual_matched - profile_skill_ids_for_fixture)

        candidates_by_id: dict[int, dict[str, object]] = {}
        for candidate in cast(list[dict[str, object]], raw_query["candidates"]):
            candidate_id = candidate["id"]
            assert isinstance(candidate_id, int)
            candidates_by_id[candidate_id] = candidate
        for negative_case in cast(list[dict[str, str | int]], raw_query["negative_cases"]):
            candidate_id = negative_case["candidate_id"]
            assert isinstance(candidate_id, int)
            candidate = candidates_by_id[candidate_id]
            assert negative_case["type"] in cast(list[str], candidate["semantic_cases"])
            assert str(negative_case["skill_or_phrase"]).lower() in json.dumps(candidate).lower()

    assert fmean(vector_ndcgs) < 1.0
    assert fmean(hybrid_ndcgs) >= fmean(vector_ndcgs)
    assert fmean(fit_ndcgs) >= fmean(vector_ndcgs)
    assert fmean(hybrid_recalls) >= fmean(vector_recalls)
    assert fmean(fit_recalls) >= fmean(vector_recalls)
    assert false_positives == 0
