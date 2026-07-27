"""Test-only deterministic evaluation primitives for matching ranking."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import cast

from app.domain.matching import hybrid_retrieval, scorer
from app.domain.matching.hybrid_retrieval import RetrievalCandidate
from app.domain.matching.scorer import (
    CanonicalSkill,
    DeterministicScoreInput,
    ReadinessFindings,
)


def _required(mapping: Mapping[str, object], name: str) -> object:
    if name not in mapping:
        raise ValueError(f"missing required field: {name}")
    return mapping[name]


def _int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _positive_int(value: object, name: str) -> int:
    result = _int(value, name)
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def _float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be a list of strings")
    return tuple(value)


def _optional_string(value: object, name: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value


def _skills(value: object, name: str) -> tuple[CanonicalSkill, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    result: list[CanonicalSkill] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"{name}[{index}] must be an object")
        skill_id = _string(_required(item, "skill_id"), f"{name}[{index}].skill_id")
        label = item.get("label")
        if label is not None and not isinstance(label, str):
            raise ValueError(f"{name}[{index}].label must be a string or null")
        result.append(CanonicalSkill(skill_id, label))
    return tuple(result)


def _score_input(value: object) -> DeterministicScoreInput:
    if isinstance(value, DeterministicScoreInput):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("score_input must be an object")
    readiness_value = value.get("readiness")
    readiness: ReadinessFindings | None = None
    if readiness_value is not None:
        if not isinstance(readiness_value, Mapping):
            raise ValueError("score_input.readiness must be an object or null")
        readiness = ReadinessFindings(
            blockers=_strings(readiness_value.get("blockers", []), "readiness.blockers"),
            warnings=_strings(readiness_value.get("warnings", []), "readiness.warnings"),
        )
    semantic = value.get("semantic_similarity")
    if semantic is not None:
        semantic = _float(semantic, "score_input.semantic_similarity")
    return DeterministicScoreInput(
        required_skills=_skills(value.get("required_skills", []), "required_skills"),
        matched_required_skills=_skills(
            value.get("matched_required_skills", []), "matched_required_skills"
        ),
        preferred_skills=_skills(value.get("preferred_skills", []), "preferred_skills"),
        matched_preferred_skills=_skills(
            value.get("matched_preferred_skills", []), "matched_preferred_skills"
        ),
        semantic_similarity=semantic,
        job_title=_optional_string(value.get("job_title"), "job_title"),
        profile_roles=_strings(value.get("profile_roles", []), "profile_roles"),
        profile_headline=_optional_string(value.get("profile_headline"), "profile_headline"),
        job_seniority=_optional_string(value.get("job_seniority"), "job_seniority"),
        profile_seniority=_optional_string(value.get("profile_seniority"), "profile_seniority"),
        job_years_required=(
            _float(value["job_years_required"], "job_years_required")
            if value.get("job_years_required") is not None
            else None
        ),
        profile_years_experience=(
            _float(value["profile_years_experience"], "profile_years_experience")
            if value.get("profile_years_experience") is not None
            else None
        ),
        readiness=readiness,
    )


@dataclass(frozen=True)
class SyntheticCandidate:
    """One fully synthetic candidate in an evaluation query."""

    candidate_id: int
    relevance_grade: int
    vector_rank: int
    vector_score: float
    text_rank: int | None
    text_score: float | None
    skill_rank: int | None
    skill_score: float | None
    score_input: DeterministicScoreInput

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SyntheticCandidate:
        candidate_id = _positive_int(_required(value, "candidate_id"), "candidate_id")
        grade = _int(_required(value, "relevance_grade"), "relevance_grade")
        if grade < 0 or grade > 3:
            raise ValueError("relevance_grade must be between 0 and 3")

        retrieval = _required(value, "retrieval")
        if not isinstance(retrieval, Mapping):
            raise ValueError("retrieval must be an object")

        def source(name: str) -> tuple[int | None, float | None]:
            source_value = retrieval.get(name)
            if source_value is None:
                return None, None
            if not isinstance(source_value, Mapping):
                raise ValueError(f"retrieval.{name} must be an object")
            rank = _positive_int(_required(source_value, "rank"), f"retrieval.{name}.rank")
            score = _float(_required(source_value, "score"), f"retrieval.{name}.score")
            return rank, score

        vector_rank, vector_score = source("vector")
        if vector_rank is None or vector_score is None:
            raise ValueError("retrieval.vector is required")
        text_rank, text_score = source("text")
        skill_rank, skill_score = source("skill")
        return cls(
            candidate_id=candidate_id,
            relevance_grade=grade,
            vector_rank=vector_rank,
            vector_score=vector_score,
            text_rank=text_rank,
            text_score=text_score,
            skill_rank=skill_rank,
            skill_score=skill_score,
            score_input=_score_input(_required(value, "score_input")),
        )


@dataclass(frozen=True)
class SyntheticQuery:
    """A synthetic profile and its candidate jobs."""

    query_id: str
    candidates: tuple[SyntheticCandidate, ...]
    rrf_k: int = 60

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SyntheticQuery:
        query_id = _string(_required(value, "query_id"), "query_id")
        raw_candidates = _required(value, "candidates")
        if not isinstance(raw_candidates, list):
            raise ValueError("candidates must be a list")
        candidates = tuple(
            SyntheticCandidate.from_mapping(item)
            for item in raw_candidates
            if isinstance(item, Mapping)
        )
        if len(candidates) != len(raw_candidates):
            raise ValueError("each candidate must be an object")
        ids = [candidate.candidate_id for candidate in candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate IDs must be unique")
        expected_vector_ranks = {
            candidate.candidate_id: index
            for index, candidate in enumerate(
                sorted(candidates, key=lambda item: (-item.vector_score, item.candidate_id)),
                start=1,
            )
        }
        for candidate in candidates:
            if candidate.vector_rank != expected_vector_ranks[candidate.candidate_id]:
                raise ValueError(
                    "vector_rank must match vector_score descending then candidate ID ascending"
                )
        for source_name in ("vector_rank", "text_rank", "skill_rank"):
            ranks = [
                getattr(candidate, source_name)
                for candidate in candidates
                if getattr(candidate, source_name) is not None
            ]
            if len(ranks) != len(set(ranks)):
                raise ValueError(f"{source_name} values must be unique")
        rrf_k = _positive_int(value.get("rrf_k", 60), "rrf_k")
        return cls(query_id=query_id, candidates=candidates, rrf_k=rrf_k)


def vector_ranking(query: SyntheticQuery) -> list[SyntheticCandidate]:
    """Return the declared vector baseline with deterministic ties."""
    return sorted(query.candidates, key=lambda item: (-item.vector_score, item.candidate_id))


def hybrid_ranking(query: SyntheticQuery) -> list[SyntheticCandidate]:
    """Return the RRF order using the production pure fusion helper."""
    by_id = {candidate.candidate_id: candidate for candidate in query.candidates}
    sources: dict[str, list[RetrievalCandidate]] = {}
    vector_candidates = vector_ranking(query)
    sources["vector"] = [
        RetrievalCandidate(candidate.candidate_id, candidate.vector_score)
        for candidate in vector_candidates
    ]
    for source_name, rank_name, score_name in (
        ("text", "text_rank", "text_score"),
        ("skill", "skill_rank", "skill_score"),
    ):
        source_candidates = sorted(
            (
                candidate
                for candidate in query.candidates
                if getattr(candidate, rank_name) is not None
            ),
            key=lambda item: (cast(int, getattr(item, rank_name)), item.candidate_id),
        )
        if source_candidates:
            sources[source_name] = [
                RetrievalCandidate(
                    candidate.candidate_id, cast(float, getattr(candidate, score_name))
                )
                for candidate in source_candidates
            ]
    fused = hybrid_retrieval.fuse_ranked_candidates(sources, k=query.rrf_k)
    return [by_id[item.job_id] for item in fused]


def prospective_fit_ranking(query: SyntheticQuery) -> list[SyntheticCandidate]:
    """Score the synthetic candidates without activating runtime fit mode."""
    scored = []
    for candidate in query.candidates:
        result = scorer.score_match(
            replace(candidate.score_input, semantic_similarity=candidate.vector_score)
        )
        scored.append((candidate, result.ranking_score))
    return [
        candidate
        for candidate, _ in sorted(scored, key=lambda item: (-item[1], item[0].candidate_id))
    ]


def ndcg_at_10(ranked: Sequence[SyntheticCandidate]) -> float:
    """Calculate graded nDCG@10 for a ranked synthetic candidate list."""
    def dcg(items: Sequence[SyntheticCandidate]) -> float:
        terms: list[float] = [
            (2**item.relevance_grade - 1) / math.log2(position + 2)
            for position, item in enumerate(items[:10])
        ]
        return sum(terms)

    ideal = sorted(ranked, key=lambda item: (-item.relevance_grade, item.candidate_id))
    ideal_dcg = dcg(ideal)
    return dcg(ranked) / ideal_dcg if ideal_dcg else 0.0


def recall_at_50(ranked: Sequence[SyntheticCandidate]) -> float:
    """Calculate Recall@50 using relevance grades two and three."""
    relevant = {item.candidate_id for item in ranked if item.relevance_grade >= 2}
    if not relevant:
        return 1.0
    retrieved = {item.candidate_id for item in ranked[:50]}
    return len(relevant & retrieved) / len(relevant)
