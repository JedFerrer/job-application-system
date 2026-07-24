"""Unit tests for deterministic readiness summaries on job matches."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.matching.readiness import build_match_readiness
from app.domain.matching.schemas import (
    ClassifiedSkillEvidence,
    MatchEvidence,
    SkillEvidence,
    SkillEvidenceGroup,
)
from app.domain.matching.service import MatchingService

pytestmark = pytest.mark.unit


def _service(repo: object) -> MatchingService:
    service = MatchingService.__new__(MatchingService)
    service._repo = repo  # type: ignore[attr-defined,assignment]
    service._readiness_repo = None  # type: ignore[attr-defined]
    service._embedding_coordinator = None  # type: ignore[attr-defined]
    return service


@pytest.mark.asyncio
async def test_analyze_job_embeds_an_unembedded_visible_job_without_llm() -> None:
    job = SimpleNamespace(description="Python role", description_embedding=None)
    profile = SimpleNamespace(id=3, embedding=[0.1, 0.2])
    upserted: dict[str, object] = {}

    class FakeRepository:
        async def get_active_profile(self, user_id: int):  # noqa: ANN001
            return profile

        async def get_visible_job(self, *, user_id: int, job_id: int):  # noqa: ANN001
            return job

        async def score_visible_job(self, **kwargs: object) -> float:  # noqa: ANN003
            assert kwargs["job_id"] == 101
            return 0.72

        async def upsert_match(self, **kwargs: object) -> None:  # noqa: ANN003
            for key, value in kwargs.items():
                upserted[key] = value

    class FakeCoordinator:
        async def ensure_embedding(self, job_id: int):  # noqa: ANN001
            assert job_id == 101
            return SimpleNamespace(status="ready")

    service = _service(FakeRepository())
    service._embedding_coordinator = FakeCoordinator()  # type: ignore[attr-defined,assignment]

    async def _summary(*, user_id: int, job_id: int):  # noqa: ANN001
        return SimpleNamespace(job_id=job_id)

    service.get_match_summary = _summary  # type: ignore[method-assign]
    summary = await service.analyze_job(user_id=1, job_id=101)

    assert summary.job_id == 101
    assert upserted["origin"] == "manual"
    assert upserted["vector_score"] == 72.0


def _match(**kwargs: object) -> SimpleNamespace:
    base: dict[str, object] = {
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
    for key, value in kwargs.items():
        base[key] = value
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
async def test_match_summary_includes_saved_ai_explanation() -> None:
    match = _match(
        llm_score=84,
        llm_confidence=0.9,
        llm_explanation="Strong Python and API experience for this role.",
        missing_keywords=[],
    )

    class FakeRepo:
        async def get_visible_job(self, *, user_id: int, job_id: int):  # noqa: ANN001
            return SimpleNamespace()

        async def get_match(self, *, user_id: int, job_id: int):  # noqa: ANN001
            return match

    summary = await _service(FakeRepo()).get_match_summary(user_id=1, job_id=101)

    assert summary is not None
    assert summary.explanation == "Strong Python and API experience for this role."


def test_readiness_uses_llm_confidence_with_a_saved_llm_score() -> None:
    match = _match(
        vector_score=39.45,
        confidence="weak",
        llm_score=80,
        llm_confidence=0.9,
        missing_keywords=[],
    )

    readiness = MatchingService._build_readiness(match)

    assert readiness.decision == "apply"
    assert readiness.reasons[0] == "Strong match score"


@pytest.mark.asyncio
async def test_analyze_skills_computes_and_persists_without_llm() -> None:
    match = _match(matched_skills=None, missing_keywords=None, deterministic_match=None)
    job = SimpleNamespace(
        description=(
            "Required skills:\n"
            "- Python\n"
            "- FastAPI\n"
            "- Kubernetes\n"
            "Preferred:\n"
            "- Docker\n"
            "GraphQL experience"
        )
    )
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
    assert saved["deterministic_match"]["missing_skills"] == ["kubernetes"]  # type: ignore[index]


@pytest.mark.asyncio
async def test_persisted_classified_mentions_limit_missing_keywords_to_required_gaps() -> None:
    class FakeMentionRepository:
        async def list_for_job(self, job_id: int):  # noqa: ANN001
            return [
                SimpleNamespace(canonical_label="python", classification="required"),
                SimpleNamespace(canonical_label="kubernetes", classification="required"),
                SimpleNamespace(canonical_label="docker", classification="preferred"),
                SimpleNamespace(canonical_label="graphql", classification="unclassified"),
            ]

    service = _service(object())
    service._mention_repo = FakeMentionRepository()  # type: ignore[attr-defined]

    matched, missing, deterministic = await service._deterministic_match_fields(
        job_id=101,
        job_description="ignored because persisted evidence exists",
        profile_parsed={"skills": ["Python", "Docker", "GraphQL"]},
    )

    assert matched == ["python", "docker", "graphql"]
    assert missing == ["kubernetes"]
    assert deterministic["missing_skills"] == ["kubernetes"]


@pytest.mark.asyncio
async def test_cached_match_outputs_add_classified_skills_without_changing_flat_fields() -> None:
    match = _match(missing_keywords=["legacy-gap"])
    service = _service(object())
    service._mention_repo = object()  # type: ignore[attr-defined]
    service.get_match_evidence = AsyncMock(  # type: ignore[method-assign]
        return_value=MatchEvidence(
            job_id=101,
            skills=ClassifiedSkillEvidence(
                required=SkillEvidenceGroup(
                    matched=[SkillEvidence(skill="python", job_excerpt="Required Python")],
                    missing=[SkillEvidence(skill="kubernetes", job_excerpt="Required Kubernetes")],
                ),
                preferred=SkillEvidenceGroup(
                    missing=[SkillEvidence(skill="docker", job_excerpt="Preferred Docker")]
                ),
                unclassified=SkillEvidenceGroup(
                    matched=[SkillEvidence(skill="graphql", job_excerpt="GraphQL")]
                ),
            ),
        )
    )

    explanation = await service._to_explanation(match, user_id=1)
    analysis = await service._to_skill_analysis(match, user_id=1)

    assert explanation.missing_keywords == ["legacy-gap"]
    assert analysis.missing_keywords == ["legacy-gap"]
    assert explanation.classified_skills is not None
    assert explanation.classified_skills.model_dump() == {
        "required_matched_skills": ["python"],
        "required_missing_skills": ["kubernetes"],
        "preferred_matched_skills": [],
        "preferred_missing_skills": ["docker"],
        "unclassified_skills": ["graphql"],
    }
    assert analysis.classified_skills == explanation.classified_skills


@pytest.mark.asyncio
async def test_match_summary_adds_classified_skills_when_evidence_is_available() -> None:
    match = _match(missing_keywords=["legacy-gap"])

    class FakeRepo:
        async def get_visible_job(self, **kwargs: object) -> object:  # noqa: ANN001
            return SimpleNamespace()

        async def get_match(self, **kwargs: object) -> object:  # noqa: ANN001
            return match

    service = _service(FakeRepo())
    service._mention_repo = object()  # type: ignore[attr-defined]
    service.get_match_evidence = AsyncMock(  # type: ignore[method-assign]
        return_value=MatchEvidence(
            job_id=101,
            skills=ClassifiedSkillEvidence(
                required=SkillEvidenceGroup(
                    missing=[SkillEvidence(skill="kubernetes", job_excerpt="Required")]
                )
            ),
        )
    )

    summary = await service.get_match_summary(user_id=1, job_id=101)

    assert summary is not None
    assert summary.missing_keywords == ["legacy-gap"]
    assert summary.classified_skills is not None
    assert summary.classified_skills.required_missing_skills == ["kubernetes"]


@pytest.mark.asyncio
async def test_llm_receives_deterministic_classified_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    async def fake_completion(**kwargs: object) -> tuple[object, object]:
        captured.extend(kwargs["messages"])  # type: ignore[arg-type,index]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"score": 80}'))]
        ), None

    monkeypatch.setattr("app.domain.matching.service.llm_chat_completion", fake_completion)
    service = _service(object())
    classified = {"required_missing_skills": ["kubernetes"]}

    await service._explain_with_llm(
        profile_parsed={},
        job_description="Job text",
        report_dict={"classified_skills": classified},
        deterministic_missing=["kubernetes"],
    )

    payload = json.loads(captured[1]["content"])
    assert payload["classified_skills"] == classified


def test_llm_missing_keywords_cannot_override_deterministic_gaps() -> None:
    _, _, missing, _, _, _ = MatchingService._parse_llm_payload(
        {
            "score": 80,
            "confidence": "strong",
            "missing_keywords": ["invented-skill", "preferred-only-skill"],
        },
        ["required-gap"],
    )

    assert missing == ["required-gap"]
