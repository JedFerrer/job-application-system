"""Unit tests for deterministic readiness summaries on job matches."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.matching.readiness import build_match_readiness
from app.domain.matching.service import MatchingService

pytestmark = pytest.mark.unit


def _service(repo: object) -> MatchingService:
    service = MatchingService.__new__(MatchingService)
    service._repo = repo  # type: ignore[attr-defined]
    return service


def _match(**kwargs: object) -> SimpleNamespace:
    base = {
        "job_id": 101,
        "vector_score": 88.0,
        "llm_score": None,
        "confidence": "moderate",
        "llm_confidence": None,
        "llm_explanation": None,
        "matched_skills": ["python", "fastapi"],
        "missing_keywords": None,
        "deterministic_match": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_build_match_readiness_apply_for_strong_match_without_known_gaps() -> None:
    readiness = build_match_readiness(
        score=92,
        confidence="strong",
        matched_skills=["python", "fastapi"],
        missing_keywords=[],
    )

    assert readiness.decision == "apply"
    assert readiness.reasons == ["Strong match score", "2 matched skills found"]
    assert readiness.caveats == []


def test_build_match_readiness_keeps_cautious_save_when_gap_evidence_is_missing() -> None:
    readiness = build_match_readiness(
        score=72,
        confidence="moderate",
        matched_skills=["python"],
        missing_keywords=None,
    )

    assert readiness.decision == "save"
    assert readiness.reasons == ["Moderate match score", "1 matched skills found"]
    assert readiness.caveats == ["Detailed skill gaps are not available yet"]


def test_build_match_readiness_downgrades_apply_when_small_known_gaps_exist() -> None:
    readiness = build_match_readiness(
        score=88,
        confidence="strong",
        matched_skills=["python"],
        missing_keywords=["kubernetes"],
    )

    assert readiness.decision == "save"
    assert readiness.reasons == [
        "Strong match score",
        "1 matched skills found",
        "Known gaps: kubernetes",
    ]
    assert readiness.caveats == []


def test_build_match_readiness_marks_large_known_gaps_as_stretch() -> None:
    readiness = build_match_readiness(
        score=91,
        confidence="strong",
        matched_skills=["python"],
        missing_keywords=["kubernetes", "graphql", "aws"],
    )

    assert readiness.decision == "stretch"
    assert "Known gaps: kubernetes, graphql, aws" in readiness.reasons


def test_build_match_readiness_skips_only_for_weak_scores_or_confidence() -> None:
    readiness = build_match_readiness(
        score=45,
        confidence="moderate",
        matched_skills=[],
        missing_keywords=None,
    )

    assert readiness.decision == "skip"
    assert readiness.reasons == ["Low match score"]


def test_build_match_readiness_skips_when_confidence_is_weak() -> None:
    readiness = build_match_readiness(
        score=72,
        confidence="weak",
        matched_skills=["python"],
        missing_keywords=[],
    )

    assert readiness.decision == "skip"
    assert readiness.reasons == [
        "Weak match confidence",
        "Moderate match score",
        "1 matched skills found",
    ]


@pytest.mark.asyncio
async def test_list_matches_attaches_readiness_without_invoking_llm(monkeypatch) -> None:
    async def _should_not_run(*args, **kwargs):  # noqa: ANN001
        raise AssertionError("LLM must not be called for list readiness")

    monkeypatch.setattr("app.domain.matching.service.llm_chat_completion", _should_not_run)

    job = SimpleNamespace(
        id=101,
        title="Backend Engineer",
        company="Acme",
        location="Remote",
        is_remote=True,
        job_type="fulltime",
        description="Python FastAPI role",
        shift_type=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        source="test",
        source_url="https://example.com/job/101",
        company_logo=None,
        posted_at=None,
    )
    match = _match(
        vector_score=87.0,
        confidence="strong",
        llm_score=None,
        matched_skills=["python", "fastapi"],
        missing_keywords=None,
    )

    class FakeRepo:
        async def list_matches(self, **kwargs):  # noqa: ANN001
            return [(job, match)], 1, None, False

    service = _service(FakeRepo())

    response = await service.list_matches(user_id=1, limit=50)

    item = response.items[0]
    assert item.readiness.decision == "apply"
    assert item.readiness.reasons[0] == "Strong match score"
    assert item.readiness.caveats == ["Detailed skill gaps are not available yet"]


@pytest.mark.asyncio
async def test_explain_match_returns_cached_readiness() -> None:
    match = _match(
        job_id=202,
        vector_score=71.0,
        llm_score=84,
        confidence="strong",
        llm_confidence=0.9,
        llm_explanation="cached explanation",
        matched_skills=["python", "fastapi"],
        missing_keywords=["kubernetes", "graphql"],
    )

    class FakeRepo:
        async def get_match(self, *, user_id: int, job_id: int):  # noqa: ANN001
            assert user_id == 1
            assert job_id == 202
            return match

    service = _service(FakeRepo())

    explanation = await service.explain_match(user_id=1, job_id=202)

    assert explanation.readiness.decision == "save"
    assert explanation.readiness.reasons[0] == "Strong match score"
    assert "Known gaps: kubernetes, graphql" in explanation.readiness.reasons


@pytest.mark.asyncio
async def test_analyze_skills_computes_and_persists_without_llm() -> None:
    match = _match(matched_skills=None, missing_keywords=None, deterministic_match=None)
    job = SimpleNamespace(description="Python FastAPI Kubernetes role")
    profile = SimpleNamespace(parsed={"skills": ["Python", "FastAPI"]})
    saved: dict[str, object] = {}

    class FakeRepo:
        async def get_match(self, *, user_id: int, job_id: int):  # noqa: ANN001
            return match

        async def get_visible_job(self, *, user_id: int, job_id: int):  # noqa: ANN001
            return job

        async def get_active_profile(self, user_id: int):  # noqa: ANN001
            return profile

        async def save_deterministic_match(self, **kwargs):  # noqa: ANN001
            saved.update(kwargs)
            match.matched_skills = kwargs["matched_skills"]
            match.missing_keywords = kwargs["missing_keywords"]
            match.deterministic_match = kwargs["deterministic_match"]
            return match

    analysis = await _service(FakeRepo()).analyze_skills(user_id=1, job_id=101)

    assert analysis.matched_skills == ["python", "fastapi"]
    assert analysis.missing_keywords == ["kubernetes"]
    assert saved["deterministic_match"]
