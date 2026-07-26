"""Unit tests for deterministic readiness summaries on job matches."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.domain.matching.catalog import CATALOG_VERSION
from app.domain.matching.llm_fingerprint import (
    canonical_llm_prompt_payload,
    llm_input_hash_for_payload,
)
from app.domain.matching.profile_fingerprint import profile_matching_hash
from app.domain.matching.prompts import MATCH_EXPLANATION_PROMPT
from app.domain.matching.readiness import (
    ReadinessContext,
    build_match_readiness,
    readiness_preference_hash,
    select_readiness_score,
)
from app.domain.matching.readiness_preferences import ReadinessPreferenceRepository
from app.domain.matching.schemas import (
    ClassifiedSkillEvidence,
    MatchEvidence,
    SkillEvidence,
    SkillEvidenceGroup,
)
from app.domain.matching.scorer import SCORER_VERSION
from app.domain.matching.service import MatchingService, job_description_hash
from app.infrastructure.llm_client import (
    LLMCompletionMetadata,
    LLMRequestSpec,
    LLMUnavailableError,
    normalize_model_identity,
    resolve_llm_request_spec,
)
from app.models.user_job_match import UserJobMatch

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
    assert isinstance(upserted["fit_score"], float)
    assert isinstance(upserted["ranking_score"], float)
    assert isinstance(upserted["score_components"], dict)
    assert upserted["source_profile_id"] == 3


def _match(**kwargs: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "job_id": 101,
        "vector_score": 88.0,
        "llm_score": None,
        "confidence": "moderate",
        "llm_confidence": None,
        "llm_explanation": None,
        "llm_analyzed_at": None,
        "llm_input_hash": None,
        "matched_skills": ["python", "fastapi"],
        "missing_keywords": None,
        "deterministic_match": None,
        "fit_score": None,
        "ranking_score": None,
        "score_components": None,
        "source_profile_id": None,
        "source_profile_matching_hash": None,
        "job_description_hash": None,
        "ontology_version": None,
        "scorer_version": None,
        "readiness_preferences_hash": None,
    }
    for key, value in kwargs.items():
        base[key] = value
    return SimpleNamespace(**base)


def _fresh_readiness_state(
    *, origin: str = "automatic"
) -> tuple[SimpleNamespace, ReadinessContext]:
    profile = SimpleNamespace(id=3, parsed={"skills": ["Python"]})
    job = SimpleNamespace(description="Python role")
    match = _match(
        vector_score=39.0,
        fit_score=81.0,
        ranking_score=77.0,
        score_components={"components": {}},
        source_profile_id=profile.id,
        source_profile_matching_hash=profile_matching_hash(profile.parsed),
        job_description_hash=job_description_hash(job.description),
        ontology_version=CATALOG_VERSION,
        scorer_version=SCORER_VERSION,
        readiness_preferences_hash=readiness_preference_hash(None),
        origin=origin,
    )
    context = MatchingService._readiness_context(
        job=job,
        active_profile=profile,
        preferences=None,
        preferences_loaded=True,
        current_profile_hash=profile_matching_hash(profile.parsed),
    )
    return match, context


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
async def test_list_matches_attaches_readiness_without_invoking_llm() -> None:
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
        async def list_matches(
            self, **kwargs: object
        ) -> tuple[list[tuple[SimpleNamespace, SimpleNamespace]], int, None, bool]:
            return [(job, match)], 1, None, False

    service = _service(FakeRepo())

    response = await service.list_matches(user_id=1, limit=50)

    item = response.items[0]
    assert item.readiness.decision == "apply"
    assert item.readiness.reasons[0] == "Strong match score"
    assert item.readiness.caveats == ["Detailed skill gaps are not available yet"]


def _readiness_job(job_id: int = 101) -> SimpleNamespace:
    return SimpleNamespace(
        id=job_id,
        title="Backend Engineer",
        company="Acme",
        location="Remote",
        is_remote=True,
        job_type="fulltime",
        description="Python role",
        shift_type=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        source="test",
        source_url=f"https://example.com/job/{job_id}",
        company_logo=None,
        posted_at=None,
    )


class _CountingReadinessPreferences:
    def __init__(self) -> None:
        self.calls = 0

    async def get_for_user(self, user_id: int) -> None:
        self.calls += 1
        return None


class _CountingListRepository:
    def __init__(self, rows: list[tuple[SimpleNamespace, SimpleNamespace]]) -> None:
        self.rows = rows
        self.profile_calls = 0

    async def list_matches(
        self, **kwargs: object
    ) -> tuple[list[tuple[SimpleNamespace, SimpleNamespace]], int, None, bool]:
        return self.rows, len(self.rows), None, False

    async def get_active_profile(self, user_id: int) -> SimpleNamespace:
        self.profile_calls += 1
        return SimpleNamespace(id=3, parsed={"skills": ["Python"]})


@pytest.mark.asyncio
async def test_list_readiness_loads_profile_and_preferences_once_for_multiple_matches() -> None:
    first_job = _readiness_job(101)
    second_job = _readiness_job(102)
    first_match, _ = _fresh_readiness_state()
    second_match, _ = _fresh_readiness_state()
    first_match.job_id = first_job.id
    second_match.job_id = second_job.id
    first_match.job_description_hash = job_description_hash(first_job.description)
    second_match.job_description_hash = job_description_hash(second_job.description)
    repository = _CountingListRepository([(first_job, first_match), (second_job, second_match)])
    preferences = _CountingReadinessPreferences()
    service = _service(repository)
    service._readiness_repo = cast(ReadinessPreferenceRepository, preferences)

    response = await service.list_matches(user_id=1, limit=50)

    assert len(response.items) == 2
    assert repository.profile_calls == 1
    assert preferences.calls == 1


@pytest.mark.asyncio
async def test_readiness_endpoints_share_equivalent_resulting_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _readiness_job()
    match, _ = _fresh_readiness_state()
    match.deterministic_match = {}
    match.missing_keywords = []
    profile = SimpleNamespace(id=3, parsed={"skills": ["Python"]})
    profile.embedding = [0.1]
    job.description_embedding = [0.1]
    preferences = _CountingReadinessPreferences()

    class Repository:
        async def list_matches(
            self, **kwargs: object
        ) -> tuple[list[tuple[SimpleNamespace, SimpleNamespace]], int, None, bool]:
            return [(job, match)], 1, None, False

        async def get_match(self, *, user_id: int, job_id: int) -> SimpleNamespace:
            return match

        async def get_visible_job(self, *, user_id: int, job_id: int) -> SimpleNamespace:
            return job

        async def get_active_profile(self, user_id: int) -> SimpleNamespace:
            return profile

        async def score_visible_job(
            self, *, user_id: int, job_id: int, embedding: list[float]
        ) -> float:
            return 0.39

        async def upsert_match(self, **kwargs: object) -> None:
            for key, value in kwargs.items():
                setattr(match, key, value)

    service = _service(Repository())
    service._readiness_repo = cast(ReadinessPreferenceRepository, preferences)
    service._deterministic_match_fields = AsyncMock(  # type: ignore[method-assign]
        return_value=(match.matched_skills, match.missing_keywords or [], {})
    )
    monkeypatch.setattr(
        MatchingService,
        "_has_reusable_llm_cache",
        staticmethod(lambda match, expected_hash: True),
    )
    service._score_match_inputs = AsyncMock(  # type: ignore[method-assign]
        return_value=(
            {
                "fit_score": match.fit_score,
                "ranking_score": match.ranking_score,
                "score_components": match.score_components,
            },
            {
                "source_profile_id": profile.id,
                "source_profile_matching_hash": profile_matching_hash(profile.parsed),
                "job_description_hash": job_description_hash(job.description),
                "ontology_version": CATALOG_VERSION,
                "scorer_version": SCORER_VERSION,
                "readiness_preferences_hash": readiness_preference_hash(None),
            },
        )
    )

    listed = (await service.list_matches(user_id=1, limit=50)).items[0].readiness
    summary = await service.get_match_summary(user_id=1, job_id=job.id)
    skills = await service.analyze_skills(user_id=1, job_id=job.id)
    explanation = await service.explain_match(user_id=1, job_id=job.id)
    analyzed = await service.analyze_job(user_id=1, job_id=job.id)

    assert summary is not None
    assert summary.readiness == listed
    assert skills.readiness == listed
    assert explanation.readiness == listed
    assert analyzed.readiness == listed


@pytest.mark.asyncio
async def test_explain_match_returns_cached_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Settings:
        llm_model = "gpt-4o-mini"

        def get_fallback_chain(self) -> list[dict[str, str | None]]:
            return [{"name": "openai", "api_key": "key", "base_url": None, "model": None}]

    settings = Settings()
    monkeypatch.setattr("app.domain.matching.service.get_settings", lambda: settings)
    monkeypatch.setattr("app.infrastructure.llm_client.get_settings", lambda: settings)
    request_spec = resolve_llm_request_spec(
        model=normalize_model_identity(settings.llm_model), temperature=0.2, json_mode=True
    )
    assert request_spec is not None
    report: dict[str, object] = {}
    prompt_payload = canonical_llm_prompt_payload(
        profile_parsed={}, job_description="", deterministic_report=report
    )
    match = _match(
        job_id=202,
        vector_score=71.0,
        llm_score=84,
        confidence="strong",
        llm_confidence=0.9,
        llm_explanation="cached explanation",
        matched_skills=["python", "fastapi"],
        missing_keywords=["kubernetes", "graphql"],
        llm_analyzed_at=datetime.now(UTC),
        llm_input_hash=llm_input_hash_for_payload(
            prompt_payload=prompt_payload,
            model="gpt-4o-mini",
            prompt_version="match-explanation-v1",
            prompt_text=MATCH_EXPLANATION_PROMPT,
            ontology_version=CATALOG_VERSION,
            scorer_version=SCORER_VERSION,
            generation_parameters=request_spec.cache_identity(),
        ),
    )

    class FakeRepo:
        async def get_match(self, *, user_id: int, job_id: int) -> SimpleNamespace:
            assert user_id == 1
            assert job_id == 202
            return match

        async def get_visible_job(self, *, user_id: int, job_id: int) -> SimpleNamespace:
            return SimpleNamespace(description="")

        async def get_active_profile(self, user_id: int) -> SimpleNamespace:
            return SimpleNamespace(parsed={}, profile_matching_hash="profile-hash")

        async def clear_llm_analysis(self, current: UserJobMatch) -> None:
            current.llm_score = None
            current.llm_confidence = None
            current.llm_explanation = None
            current.llm_analyzed_at = None
            current.llm_error = None
            current.llm_input_hash = None

    service = _service(FakeRepo())
    service._deterministic_match_fields = AsyncMock(  # type: ignore[method-assign]
        return_value=(["python", "fastapi"], ["kubernetes", "graphql"], {})
    )
    service._load_classified_skills = AsyncMock(return_value=None)  # type: ignore[method-assign]

    adapter = AsyncMock()
    monkeypatch.setattr("app.domain.matching.service.llm_chat_completion_for_spec", adapter)

    explanation = await service.explain_match(user_id=1, job_id=202)

    assert explanation.readiness.decision == "save"
    assert explanation.readiness.reasons[0] == "Moderate match score"
    assert "Known gaps: kubernetes, graphql" in explanation.readiness.reasons
    adapter.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_hash", [None, "stale-hash"])
async def test_explain_match_does_not_use_missing_or_stale_hash(
    stored_hash: str | None,
) -> None:
    match = _match(
        job_id=203,
        llm_score=84,
        llm_explanation="stale",
        llm_analyzed_at=datetime.now(UTC),
        llm_input_hash=stored_hash,
    )

    class FakeRepo:
        async def get_match(self, *, user_id: int, job_id: int):  # noqa: ANN001
            return match

        async def get_visible_job(self, *, user_id: int, job_id: int):  # noqa: ANN001
            return SimpleNamespace(description="")

        async def get_active_profile(self, user_id: int):  # noqa: ANN001
            return SimpleNamespace(parsed={})

        async def clear_llm_analysis(self, match):  # noqa: ANN001
            return None

        async def save_explanation(self, **kwargs):  # noqa: ANN001
            return None

    service = _service(FakeRepo())
    service._deterministic_match_fields = AsyncMock(  # type: ignore[method-assign]
        return_value=([], [], {})
    )
    service._explain_with_llm = AsyncMock(  # type: ignore[method-assign]
        return_value=(80, 0.8, [], "fresh", "strong", None, True)
    )
    service._to_explanation = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace()
    )

    await service.explain_match(user_id=1, job_id=203)

    service._explain_with_llm.assert_awaited_once()  # type: ignore[attr-defined]
    service._to_explanation.assert_awaited_once()  # type: ignore[attr-defined]


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


def test_readiness_ignores_llm_score_and_confidence() -> None:
    match = _match(
        vector_score=39.45,
        confidence="weak",
        llm_score=80,
        llm_confidence=0.9,
        missing_keywords=[],
    )

    readiness = MatchingService._build_readiness(match)

    assert readiness.decision == "skip"
    assert readiness.reasons[0] == "Low match score"


def test_current_deterministic_row_uses_fit_score_only() -> None:
    match, context = _fresh_readiness_state()
    match.llm_score = 99
    match.llm_confidence = 0.99

    readiness = MatchingService._build_readiness(match, readiness_context=context)

    assert readiness.decision == "apply"
    assert readiness.reasons[0] == "Strong match score"


@pytest.mark.parametrize(
    ("field", "mismatched_value"),
    [
        ("fit_score", None),
        ("ranking_score", None),
        ("score_components", None),
        ("source_profile_id", 999),
        ("source_profile_matching_hash", "different-profile-hash"),
        ("job_description_hash", "different-job-hash"),
        ("ontology_version", "different-ontology"),
        ("scorer_version", "different-scorer"),
        ("readiness_preferences_hash", "different-preferences-hash"),
    ],
)
def test_legacy_or_stale_deterministic_row_uses_vector_fallback(
    field: str, mismatched_value: object
) -> None:
    match, context = _fresh_readiness_state()
    setattr(match, field, mismatched_value)

    readiness = MatchingService._build_readiness(match, readiness_context=context)

    assert readiness.decision == "skip"
    assert readiness.reasons[0] == "Low match score"


@pytest.mark.parametrize(
    "field",
    [
        "source_profile_id",
        "source_profile_matching_hash",
        "job_description_hash",
        "ontology_version",
        "scorer_version",
        "readiness_preferences_hash",
    ],
)
def test_legacy_null_provenance_uses_vector_fallback(field: str) -> None:
    match, context = _fresh_readiness_state()
    setattr(match, field, None)

    readiness = MatchingService._build_readiness(match, readiness_context=context)

    assert readiness.decision == "skip"
    assert readiness.reasons[0] == "Low match score"


def test_legacy_null_active_profile_hash_is_recomputed_without_writing() -> None:
    match, context = _fresh_readiness_state()
    profile = cast(SimpleNamespace, context.active_profile)
    profile.profile_matching_hash = None

    readiness = MatchingService._build_readiness(match, readiness_context=context)

    assert readiness.decision == "apply"
    assert profile.profile_matching_hash is None


def test_missing_active_profile_or_preferences_context_uses_vector_fallback() -> None:
    match, context = _fresh_readiness_state()
    unavailable = context.__class__(
        active_profile=None,
        current_profile_matching_hash=None,
        preferences=None,
        current_readiness_preferences_hash=None,
        preferences_loaded=False,
        job=context.job,
        current_job_description_hash=context.current_job_description_hash,
    )

    readiness = MatchingService._build_readiness(match, readiness_context=unavailable)

    assert readiness.decision == "skip"
    assert readiness.reasons[0] == "Low match score"


def test_non_finite_fit_uses_finite_vector_fallback() -> None:
    match, context = _fresh_readiness_state()
    match.fit_score = float("nan")

    readiness = MatchingService._build_readiness(match, readiness_context=context)

    assert readiness.decision == "skip"
    assert readiness.reasons[0] == "Low match score"


def test_missing_vector_preserves_unavailable_score_behavior() -> None:
    match, context = _fresh_readiness_state()
    match.vector_score = None
    match.fit_score = None

    readiness = MatchingService._build_readiness(match, readiness_context=context)

    assert readiness.decision == "save"
    assert readiness.reasons[0] == "Match score unavailable"
    assert "Match score is not available yet" in readiness.caveats


@pytest.mark.parametrize(
    ("llm_score", "llm_confidence", "llm_error", "llm_input_hash"),
    [
        (99, 0.99, None, "cache-hit"),
        (1, 0.1, None, "cache-hit"),
        (None, None, None, None),
        (99, 0.99, "provider failed", None),
        (99, 0.99, None, "stale-cache"),
    ],
)
def test_fresh_fit_readiness_is_independent_of_llm_state(
    llm_score: int | None,
    llm_confidence: float | None,
    llm_error: str | None,
    llm_input_hash: str | None,
) -> None:
    match, context = _fresh_readiness_state()
    match.llm_score = llm_score
    match.llm_confidence = llm_confidence
    match.llm_error = llm_error
    match.llm_input_hash = llm_input_hash
    match.llm_explanation = "advisory" if llm_score is not None else None
    match.llm_analyzed_at = datetime.now(UTC) if llm_input_hash == "cache-hit" else None

    readiness = MatchingService._build_readiness(match, readiness_context=context)

    assert readiness.decision == "apply"
    assert readiness.reasons == ["Strong match score", "2 matched skills found"]
    assert readiness.decision_basis == "score"


def test_stale_deterministic_fit_ignores_current_llm_score() -> None:
    match, context = _fresh_readiness_state()
    match.source_profile_id = 999
    match.llm_score = 99
    match.llm_confidence = 0.99

    readiness = MatchingService._build_readiness(match, readiness_context=context)

    assert readiness.decision == "skip"
    assert readiness.reasons[0] == "Low match score"


def test_legacy_llm_only_row_uses_vector_score() -> None:
    match, context = _fresh_readiness_state()
    for field in (
        "fit_score",
        "ranking_score",
        "score_components",
        "source_profile_id",
        "source_profile_matching_hash",
        "job_description_hash",
        "ontology_version",
        "scorer_version",
        "readiness_preferences_hash",
    ):
        setattr(match, field, None)
    match.llm_score = 99
    match.llm_confidence = 0.99

    readiness = MatchingService._build_readiness(match, readiness_context=context)

    assert readiness.decision == "skip"
    assert readiness.reasons[0] == "Low match score"


@pytest.mark.parametrize(
    ("fit_score", "vector_score", "expected_score"),
    [
        (0.0, 39.0, 0.0),
        (81.0, 0.0, 81.0),
        (True, 39.0, 39.0),
        (81.0, False, None),
        (math.inf, 39.0, 39.0),
        (-math.inf, 39.0, 39.0),
        (math.nan, 39.0, 39.0),
    ],
)
def test_readiness_score_numeric_edges(
    fit_score: object, vector_score: object, expected_score: float | None
) -> None:
    match, context = _fresh_readiness_state()
    match.fit_score = fit_score
    match.vector_score = vector_score

    selected = select_readiness_score(match, context)

    assert selected == expected_score


def test_invalid_vector_preserves_unavailable_readiness() -> None:
    match, context = _fresh_readiness_state()
    match.fit_score = 81.0
    match.vector_score = math.inf

    readiness = MatchingService._build_readiness(match, readiness_context=context)

    assert readiness.decision == "save"
    assert readiness.reasons[0] == "Match score unavailable"


def test_zero_vector_is_valid_for_stale_fallback() -> None:
    match, context = _fresh_readiness_state()
    match.source_profile_id = 999
    match.vector_score = 0.0

    assert select_readiness_score(match, context) == 0.0


@pytest.mark.parametrize("origin", ["automatic", "manual"])
@pytest.mark.parametrize("stale", [False, True])
def test_match_origin_never_bypasses_freshness(
    origin: str, stale: bool
) -> None:
    match, context = _fresh_readiness_state(origin=origin)
    if stale:
        match.source_profile_id = 999

    readiness = MatchingService._build_readiness(match, readiness_context=context)

    assert readiness.decision == ("skip" if stale else "apply")
    assert readiness.reasons[0] == ("Low match score" if stale else "Strong match score")


def test_reusable_llm_cache_requires_successful_explanation() -> None:
    match = _match(
        llm_analyzed_at=datetime.now(UTC),
        llm_input_hash="current",
        llm_explanation=None,
        llm_error="LLM service unavailable",
    )

    assert not MatchingService._has_reusable_llm_cache(match, "current")

    match.llm_explanation = "fresh"
    match.llm_error = None
    assert MatchingService._has_reusable_llm_cache(match, "current")


def test_llm_model_compatibility_requires_a_reported_exact_match() -> None:
    assert MatchingService._models_are_compatible(
        requested_model="gpt-4o-mini", actual_model="gpt-4o-mini"
    )
    assert not MatchingService._models_are_compatible(
        requested_model="gpt-4o-mini", actual_model=None
    )
    assert not MatchingService._models_are_compatible(
        requested_model="gpt-4o-mini", actual_model="gpt-4o"
    )
    assert not MatchingService._models_are_compatible(requested_model="   ", actual_model="   ")
    assert not MatchingService._models_are_compatible(requested_model=None, actual_model="model")
    assert not MatchingService._models_are_compatible(requested_model=42, actual_model="model")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actual_model", "fallback_occurred"),
    [(None, False), ("other-model", False), ("gpt-4o-mini", True)],
)
async def test_unverifiable_or_fallback_llm_result_is_not_cacheable(
    monkeypatch: pytest.MonkeyPatch,
    actual_model: str | None,
    fallback_occurred: bool,
) -> None:
    async def fake_completion(**_kwargs: object) -> tuple[object, object]:
        return (
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"score": 80, "confidence": "strong", "explanation": "ok"}'
                        )
                    )
                ]
            ),
            SimpleNamespace(
                requested_model="gpt-4o-mini",
                actual_model=actual_model,
                fallback_occurred=fallback_occurred,
            ),
        )

    monkeypatch.setattr("app.domain.matching.service.llm_chat_completion_for_spec", fake_completion)
    result = await _service(object())._explain_with_llm(
        prompt_payload={},
        deterministic_missing=["required-gap"],
        request_spec=cast(LLMRequestSpec, SimpleNamespace()),
    )

    assert result[3] == "ok"
    assert result[-1] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actual_model", "fallback", "cacheable"),
    [("gpt-4o-mini", False, True), (None, False, False), ("   ", False, False),
     ("other-model", False, False), ("gpt-4o-mini", True, False)],
)
async def test_explain_match_persists_cacheability_and_retries(
    monkeypatch: pytest.MonkeyPatch,
    actual_model: str | None,
    fallback: bool,
    cacheable: bool,
) -> None:
    match = _match(vector_score=72.0, fit_score=61.0, ranking_score=61.0)
    saves: list[dict[str, object]] = []
    calls = 0

    class Settings:
        llm_model = "gpt-4o-mini"

        def get_fallback_chain(self) -> list[dict[str, str | None]]:
            return [{"name": "openai", "api_key": "key", "base_url": None, "model": None}]

    class Repo:
        async def get_match(self, *, user_id: int, job_id: int) -> SimpleNamespace:
            return match

        async def get_visible_job(self, *, user_id: int, job_id: int) -> SimpleNamespace:
            return SimpleNamespace(description="Python role")

        async def get_active_profile(self, user_id: int) -> SimpleNamespace:
            return SimpleNamespace(parsed={})

        async def clear_llm_analysis(self, current: SimpleNamespace) -> None:
            current.llm_score = None
            current.llm_confidence = None
            current.llm_explanation = None
            current.llm_analyzed_at = None
            current.llm_error = None
            current.llm_input_hash = None

        async def save_explanation(self, **kwargs: object) -> SimpleNamespace:
            saves.append(kwargs)
            match.llm_score = kwargs["llm_score"]
            match.llm_confidence = kwargs["llm_confidence"]
            match.llm_explanation = kwargs["explanation"]
            match.llm_analyzed_at = kwargs["llm_analyzed_at"]
            match.llm_error = kwargs["llm_error"]
            match.llm_input_hash = kwargs["llm_input_hash"]
            match.matched_skills = kwargs["matched_skills"]
            match.missing_keywords = kwargs["missing_keywords"]
            match.deterministic_match = kwargs["deterministic_match"]
            return match

    async def fake_completion(**_kwargs: object) -> tuple[object, LLMCompletionMetadata]:
        nonlocal calls
        calls += 1
        return (
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content='{"score": 80, "confidence": "strong", "explanation": "ok"}'
                ))]
            ),
            LLMCompletionMetadata("openai", "gpt-4o-mini", actual_model, fallback),
        )

    monkeypatch.setattr("app.domain.matching.service.get_settings", lambda: Settings())
    monkeypatch.setattr("app.domain.matching.service.llm_chat_completion_for_spec", fake_completion)
    service = _service(Repo())
    service._deterministic_match_fields = AsyncMock(return_value=([], [], {}))  # type: ignore[method-assign]
    service._load_classified_skills = AsyncMock(return_value=None)  # type: ignore[method-assign]
    service._to_explanation = AsyncMock(return_value=SimpleNamespace())  # type: ignore[method-assign]

    await service.explain_match(user_id=1, job_id=101)
    await service.explain_match(user_id=1, job_id=101)

    assert calls == (1 if cacheable else 2)
    assert (saves[0]["llm_input_hash"] is not None) is cacheable
    assert (saves[0]["llm_analyzed_at"] is not None) is cacheable


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actual_model", "fallback", "failure"),
    [
        (None, False, True),
        (None, False, False),
        ("   ", False, False),
        ("other-model", False, False),
        ("gpt-4o-mini", True, False),
    ],
)
async def test_explain_match_failed_refresh_clears_stale_llm_state(
    monkeypatch: pytest.MonkeyPatch,
    actual_model: str | None,
    fallback: bool,
    failure: bool,
) -> None:
    match = _match(
        vector_score=72.0,
        fit_score=61.0,
        ranking_score=61.0,
        llm_score=88,
        llm_confidence=0.9,
        llm_explanation="old",
        llm_analyzed_at=datetime.now(UTC),
        llm_input_hash="old-hash",
    )
    saves: list[dict[str, object]] = []
    calls = 0

    class Settings:
        llm_model = "gpt-4o-mini"

        def get_fallback_chain(self) -> list[dict[str, str | None]]:
            return [{"name": "openai", "api_key": "key", "base_url": None, "model": None}]

    class Repo:
        async def get_match(self, *, user_id: int, job_id: int) -> SimpleNamespace:
            return match

        async def get_visible_job(self, *, user_id: int, job_id: int) -> SimpleNamespace:
            return SimpleNamespace(description="Changed role")

        async def get_active_profile(self, user_id: int) -> SimpleNamespace:
            return SimpleNamespace(parsed={})

        async def clear_llm_analysis(self, current: SimpleNamespace) -> None:
            current.llm_score = current.llm_confidence = None
            current.llm_explanation = current.llm_analyzed_at = None
            current.llm_error = current.llm_input_hash = None

        async def save_explanation(self, **kwargs: object) -> SimpleNamespace:
            saves.append(kwargs)
            match.llm_score = kwargs["llm_score"]
            match.llm_confidence = kwargs["llm_confidence"]
            match.llm_explanation = kwargs["explanation"]
            match.llm_analyzed_at = kwargs["llm_analyzed_at"]
            match.llm_error = kwargs["llm_error"]
            match.llm_input_hash = kwargs["llm_input_hash"]
            match.matched_skills = kwargs["matched_skills"]
            match.missing_keywords = kwargs["missing_keywords"]
            match.deterministic_match = kwargs["deterministic_match"]
            return match

    async def completion(**_kwargs: object) -> tuple[object, LLMCompletionMetadata]:
        nonlocal calls
        calls += 1
        if failure:
            raise LLMUnavailableError("unavailable")
        return (
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content='{"score": 80, "confidence": "strong", "explanation": "new"}'
                ))]
            ),
            LLMCompletionMetadata("openai", "gpt-4o-mini", actual_model, fallback),
        )

    monkeypatch.setattr("app.domain.matching.service.get_settings", lambda: Settings())
    monkeypatch.setattr(
        "app.domain.matching.service.llm_chat_completion_for_spec", completion
    )
    service = _service(Repo())
    service._deterministic_match_fields = AsyncMock(  # type: ignore[method-assign]
        return_value=([], ["required-gap"], {"missing_skills": ["required-gap"]})
    )
    service._load_classified_skills = AsyncMock(return_value=None)  # type: ignore[method-assign]
    service._to_explanation = AsyncMock(return_value=SimpleNamespace())  # type: ignore[method-assign]

    await service.explain_match(user_id=1, job_id=101)

    saved = saves[0]
    if failure:
        assert saved["llm_score"] is None
        assert saved["llm_confidence"] is None
        assert saved["explanation"] is None
        assert saved["llm_error"] == "LLM service unavailable"
    else:
        assert saved["llm_score"] == 80
        assert saved["llm_confidence"] == 0.9
        assert saved["explanation"] == "new"
        assert saved["llm_error"] is None
    assert saved["llm_analyzed_at"] is None
    assert saved["llm_input_hash"] is None
    assert match.llm_analyzed_at is None
    assert match.llm_input_hash is None
    assert not MatchingService._has_reusable_llm_cache(match, "old-hash")

    await service.explain_match(user_id=1, job_id=101)

    assert calls == 2
    assert len(saves) == 2
    assert saves[0]["llm_analyzed_at"] is None
    assert saves[0]["llm_input_hash"] is None
    assert match.vector_score == 72.0
    assert match.fit_score == 61.0
    assert match.ranking_score == 61.0


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
    service._mention_repo = FakeMentionRepository()  # type: ignore[attr-defined,assignment]

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
    service._mention_repo = object()  # type: ignore[attr-defined,assignment]
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
    service._mention_repo = object()  # type: ignore[attr-defined,assignment]
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
    request: dict[str, object] = {}

    async def fake_completion(**kwargs: object) -> tuple[object, object]:
        captured.extend(kwargs["messages"])  # type: ignore[arg-type,index]
        request.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"score": 80}'))]
        ), SimpleNamespace(
            fallback_occurred=False,
            actual_model="gpt-4o-mini",
            requested_model="gpt-4o-mini",
        )

    monkeypatch.setattr("app.domain.matching.service.llm_chat_completion_for_spec", fake_completion)
    service = _service(object())
    classified = {"required_missing_skills": ["kubernetes"]}

    await service._explain_with_llm(
        prompt_payload=canonical_llm_prompt_payload(
            profile_parsed={},
            job_description="Job text",
            deterministic_report={"classified_skills": classified},
        ),
        deterministic_missing=["kubernetes"],
        request_spec=cast(LLMRequestSpec, SimpleNamespace()),
    )

    payload = json.loads(cast(str, captured[1]["content"]))
    assert payload["classified_skills"] == classified
    assert request["request_spec"] is not None


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
