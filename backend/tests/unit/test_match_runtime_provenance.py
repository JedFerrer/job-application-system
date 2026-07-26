"""Phase 3B1 runtime score and freshness provenance coverage."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import ClauseElement

from app.domain.matching.job_skill_mention_repository import JobSkillMentionRepository
from app.domain.matching.profile_fingerprint import profile_matching_hash
from app.domain.matching.readiness import readiness_preference_hash
from app.domain.matching.repository import MatchingRepository
from app.domain.matching.service import (
    MatchingService,
    job_description_hash,
    llm_input_hash,
)
from app.domain.matching.skill_extractor import default_skill_extractor
from app.infrastructure.llm_client import (
    LLMProviderRequest,
    LLMRequestSpec,
    LLMUnavailableError,
    llm_chat_completion_for_spec,
    normalize_model_identity,
)
from app.models.job_skill_mention import JobSkillMention

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), ("   ", None), (42, None), (" model ", " model ")],
)
def test_model_identity_normalization_is_conservative(value: object, expected: str | None) -> None:
    assert normalize_model_identity(value) == expected


def _request_spec() -> LLMRequestSpec:
    return LLMRequestSpec(
        model="test-model",
        temperature=0.2,
        json_mode=True,
        providers=(
            LLMProviderRequest("primary", "key", None, "test-model", "provider_json_object"),
            LLMProviderRequest("fallback", "key", None, "test-model", "no_response_format"),
        ),
    )


def _fake_llm_client(create: object) -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reported_model", "expected_model"),
    [("test-model", "test-model"), (None, None), ("other-model", "other-model")],
)
async def test_spec_adapter_preserves_only_provider_reported_actual_model(
    monkeypatch: pytest.MonkeyPatch,
    reported_model: str | None,
    expected_model: str | None,
) -> None:
    create = AsyncMock(return_value=SimpleNamespace(model=reported_model))
    monkeypatch.setattr(
        "app.infrastructure.llm_client.AsyncOpenAI",
        lambda **_kwargs: _fake_llm_client(create),
    )

    _, metadata = await llm_chat_completion_for_spec(
        messages=[{"role": "user", "content": "test"}], request_spec=_request_spec()
    )

    assert metadata.provider == "primary"
    assert metadata.requested_model == "test-model"
    assert metadata.actual_model == expected_model
    assert not metadata.fallback_occurred


@pytest.mark.asyncio
async def test_spec_adapter_marks_second_provider_as_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def create(**_kwargs: object) -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            from openai import APIConnectionError

            raise APIConnectionError(request=MagicMock())
        return SimpleNamespace(model="test-model")

    monkeypatch.setattr(
        "app.infrastructure.llm_client.AsyncOpenAI",
        lambda **_kwargs: _fake_llm_client(create),
    )

    _, metadata = await llm_chat_completion_for_spec(
        messages=[{"role": "user", "content": "test"}], request_spec=_request_spec()
    )

    assert attempts == 2
    assert metadata.provider == "fallback"
    assert metadata.fallback_occurred


@pytest.mark.asyncio
async def test_spec_adapter_raises_when_all_providers_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    async def create(**_kwargs: object) -> object:
        from openai import APIConnectionError

        raise APIConnectionError(request=MagicMock())

    monkeypatch.setattr(
        "app.infrastructure.llm_client.AsyncOpenAI",
        lambda **_kwargs: _fake_llm_client(create),
    )

    with pytest.raises(LLMUnavailableError):
        await llm_chat_completion_for_spec(
            messages=[{"role": "user", "content": "test"}], request_spec=_request_spec()
        )


def test_readiness_preference_hash_is_order_insensitive_for_consumed_lists() -> None:
    first = SimpleNamespace(
        minimum_annual_salary=100000,
        salary_currency="USD",
        remote_only=True,
        accepted_job_types=["full_time", "contract"],
        preferred_locations=["New York", "Remote"],
        blocked_keywords=["on-site"],
    )
    second = SimpleNamespace(
        minimum_annual_salary=100000,
        salary_currency="usd",
        remote_only=True,
        accepted_job_types=["CONTRACT", "full_time", "contract"],
        preferred_locations=["remote", "new york"],
        blocked_keywords=["ON-SITE"],
    )

    assert readiness_preference_hash(first) == readiness_preference_hash(second)
    assert readiness_preference_hash(first) != readiness_preference_hash(
        SimpleNamespace(**{**vars(first), "remote_only": False})
    )


def test_llm_input_hash_changes_when_any_cache_input_changes() -> None:
    baseline = llm_input_hash(
        profile_parsed={"skills": ["Python"]},
        job_description="Python services",
        deterministic_report={"missing_keywords": []},
        model="test-model",
    )

    assert baseline != llm_input_hash(
        profile_parsed={"skills": ["Other"]},
        job_description="Python services",
        deterministic_report={"missing_keywords": []},
        model="test-model",
    )
    assert baseline != llm_input_hash(
        profile_parsed={"skills": ["Python"]},
        job_description="Python services",
        deterministic_report={"missing_keywords": []},
        model="other-model",
    )
    assert baseline != llm_input_hash(
        profile_parsed={"skills": ["Python"]},
        job_description="Python services",
        deterministic_report={"missing_keywords": ["sql"]},
        model="test-model",
    )


def test_llm_hash_covers_full_profile_and_versioned_prompt_contract() -> None:
    profile = {"skills": ["Python"], "email": "one@example.com"}
    baseline = llm_input_hash(
        profile_parsed=profile,
        job_description="Python services",
        deterministic_report={"missing_skills": ["sql"]},
        model="test-model",
    )
    assert profile_matching_hash(profile) == profile_matching_hash(
        {**profile, "email": "two@example.com"}
    )

    assert baseline != llm_input_hash(
        profile_parsed={**profile, "email": "two@example.com"},
        job_description="Python services",
        deterministic_report={"missing_skills": ["sql"]},
        model="test-model",
    )
    assert baseline != llm_input_hash(
        profile_parsed=profile,
        job_description="Python services",
        deterministic_report={"missing_skills": ["sql"]},
        model="test-model",
        ontology_version="catalog-v2",
    )
    assert baseline != llm_input_hash(
        profile_parsed=profile,
        job_description="Python services",
        deterministic_report={"missing_skills": ["sql"]},
        model="test-model",
        prompt_version="match-explanation-v2",
    )
    assert baseline != llm_input_hash(
        profile_parsed=profile,
        job_description="Python services",
        deterministic_report={"missing_skills": ["sql"]},
        model="other-model",
    )
    assert baseline != llm_input_hash(
        profile_parsed=profile,
        job_description="Other role",
        deterministic_report={"missing_skills": ["sql"]},
        model="test-model",
    )
    assert baseline != llm_input_hash(
        profile_parsed=profile,
        job_description="Python services",
        deterministic_report={"missing_skills": ["sql"]},
        model="test-model",
        generation_parameters={"temperature": 0.3, "json_mode": True},
    )
    assert llm_input_hash(
        profile_parsed=profile,
        job_description="Python services",
        deterministic_report={"missing_skills": ["sql"]},
        model=None,
    ) is None


def test_llm_hash_normalizes_unordered_evidence() -> None:
    first = llm_input_hash(
        profile_parsed={"skills": ["Python"]},
        job_description="Python services",
        deterministic_report={
            "matched_skills": ["python", "fastapi"],
            "missing_skills": ["sql"],
        },
        model="test-model",
    )
    second = llm_input_hash(
        profile_parsed={"skills": ["Python"]},
        job_description="Python services",
        deterministic_report={
            "matched_skills": ["fastapi", "python"],
            "missing_skills": ["sql"],
        },
        model="test-model",
    )
    changed = llm_input_hash(
        profile_parsed={"skills": ["Python"]},
        job_description="Python services",
        deterministic_report={
            "matched_skills": ["python", "django"],
            "missing_skills": ["sql"],
        },
        model="test-model",
    )

    assert first == second
    assert first != changed


def test_llm_hash_covers_ordered_provider_request_policy() -> None:
    primary = llm_input_hash(
        profile_parsed={"skills": ["Python"]},
        job_description="Python services",
        deterministic_report={},
        model="test-model",
        generation_parameters={
            "policy_version": "llm-request-policy-v1",
            "providers": [
                {
                    "name": "openai",
                    "model": "test-model",
                    "response_format_mode": "provider_json_object",
                },
                {
                    "name": "anthropic",
                    "model": "test-model",
                    "response_format_mode": "no_response_format",
                },
            ],
        },
    )
    reordered = llm_input_hash(
        profile_parsed={"skills": ["Python"]},
        job_description="Python services",
        deterministic_report={},
        model="test-model",
        generation_parameters={
            "policy_version": "llm-request-policy-v1",
            "providers": [
                {
                    "name": "anthropic",
                    "model": "test-model",
                    "response_format_mode": "no_response_format",
                },
                {
                    "name": "openai",
                    "model": "test-model",
                    "response_format_mode": "provider_json_object",
                },
            ],
        },
    )

    assert primary != reordered


@pytest.mark.asyncio
async def test_runtime_score_uses_persisted_canonical_mentions() -> None:
    service = MatchingService.__new__(MatchingService)
    python = default_skill_extractor().extract("Python", source="structured").mentions[0]
    docker = default_skill_extractor().extract("Docker", source="structured").mentions[0]
    persisted_mentions = [
        replace(python, classification="required"),
        replace(docker, classification="required"),
    ]

    class MentionRepository:
        async def list_for_job(self, job_id: int) -> list[JobSkillMention]:
            return cast(list[JobSkillMention], persisted_mentions)

    service._mention_repo = cast(JobSkillMentionRepository, MentionRepository())

    profile = SimpleNamespace(
        id=4,
        parsed={"skills": ["Python"]},
        profile_matching_hash="profile-hash",
    )
    job = SimpleNamespace(id=9, title="Python Engineer", description="Python and Docker")

    score_values, provenance = await service._score_match_inputs(
        profile=profile,
        job=job,
        vector_similarity=0.8,
        preferences=None,
        job_id=job.id,
    )

    assert all(mention.skill_id for mention in persisted_mentions)
    assert all(mention.classification == "required" for mention in persisted_mentions)
    required = score_values["score_components"]["components"]["required_skill_coverage"]
    assert required["score"] == 50.0
    assert required["reason"] == "missing_required_coverage"
    assert provenance["source_profile_matching_hash"] == "profile-hash"
    assert provenance["job_description_hash"] == job_description_hash(job.description)


@pytest.mark.asyncio
async def test_changed_deterministic_evidence_clears_all_llm_fields() -> None:
    session = AsyncMock()
    repository = MatchingRepository(session)
    match = SimpleNamespace(
        matched_skills=["python"],
        missing_keywords=[],
        deterministic_match={"missing_skills": []},
        llm_score=84,
        llm_confidence=0.9,
        llm_explanation="cached",
        llm_analyzed_at=object(),
        llm_error="old error",
        llm_input_hash="old hash",
    )

    await repository.save_deterministic_match(
        match=match,  # type: ignore[arg-type]
        matched_skills=["python", "fastapi"],
        missing_keywords=["sql"],
        deterministic_match={"missing_skills": ["sql"]},
    )

    assert match.llm_score is None
    assert match.llm_confidence is None
    assert match.llm_explanation is None
    assert match.llm_analyzed_at is None
    assert match.llm_error is None
    assert match.llm_input_hash is None
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_unchanged_deterministic_evidence_preserves_llm_fields() -> None:
    session = AsyncMock()
    repository = MatchingRepository(session)
    analyzed_at = object()
    match = SimpleNamespace(
        matched_skills=["python"],
        missing_keywords=[],
        deterministic_match={"missing_skills": []},
        llm_score=84,
        llm_confidence=0.9,
        llm_explanation="cached",
        llm_analyzed_at=analyzed_at,
        llm_error=None,
        llm_input_hash="current hash",
    )

    await repository.save_deterministic_match(
        match=match,  # type: ignore[arg-type]
        matched_skills=["python"],
        missing_keywords=[],
        deterministic_match={"missing_skills": []},
    )

    assert match.llm_score == 84
    assert match.llm_confidence == 0.9
    assert match.llm_explanation == "cached"
    assert match.llm_analyzed_at is analyzed_at
    assert match.llm_input_hash == "current hash"


@pytest.mark.asyncio
async def test_evidence_order_does_not_clear_llm_fields() -> None:
    session = AsyncMock()
    repository = MatchingRepository(session)
    match = SimpleNamespace(
        matched_skills=["python", "fastapi"],
        missing_keywords=[],
        deterministic_match={"matched_skills": ["python", "fastapi"]},
        llm_score=84,
        llm_confidence=0.9,
        llm_explanation="cached",
        llm_analyzed_at=object(),
        llm_error=None,
        llm_input_hash="current hash",
    )

    await repository.save_deterministic_match(
        match=match,  # type: ignore[arg-type]
        matched_skills=["fastapi", "python"],
        missing_keywords=[],
        deterministic_match={"matched_skills": ["fastapi", "python"]},
    )

    assert match.llm_score == 84
    assert match.llm_input_hash == "current hash"


@pytest.mark.asyncio
async def test_upsert_compares_complete_deterministic_payload() -> None:
    session = AsyncMock()
    repository = MatchingRepository(session)

    await repository.upsert_match(
        user_id=1,
        job_id=2,
        vector_score=70.0,
        confidence="moderate",
        matched_skills=["python"],
        missing_keywords=["sql"],
        deterministic_match={"missing_skills": ["sql"]},
        fit_score=62.5,
        ranking_score=62.5,
        score_components={"components": {"required_skill_coverage": {"score": 50}}},
        source_profile_id=4,
        source_profile_matching_hash="profile-hash",
        job_description_hash=job_description_hash("Python services"),
        ontology_version="esco-v1.2.1",
        scorer_version="fit-scorer-v1",
        readiness_preferences_hash=readiness_preference_hash(None),
    )

    statement = cast(ClauseElement, session.execute.await_args.args[0])
    sql = str(statement.compile(dialect=postgresql.dialect()))  # type: ignore[no-untyped-call]
    assert sql.count("IS DISTINCT FROM") >= 10
    assert "llm_input_hash" in sql


@pytest.mark.asyncio
async def test_incremental_rank_persists_deterministic_fields() -> None:
    calls: list[dict[str, object]] = []

    class IncrementalRepository:
        async def get_active_profile(self, user_id: int) -> object:
            return SimpleNamespace(id=7, embedding=[0.1, 0.2], parsed={"skills": ["Python"]})

        async def score_visible_job(self, **kwargs: object) -> float:
            return 0.73

        async def get_visible_job(self, *, user_id: int, job_id: int) -> object:
            return SimpleNamespace(title="Python Engineer", description="Python services")

        async def upsert_match(self, **kwargs: object) -> None:
            calls.append(dict(kwargs))

        async def prune_automatic_matches(self, *, user_id: int, limit: int) -> None:
            return None

    ranked = await MatchingService(IncrementalRepository()).rank_job_ids(  # type: ignore[arg-type]
        user_id=1, job_ids=[9], limit=200
    )

    assert ranked == 1
    assert calls[0]["vector_score"] == 73.0
    assert isinstance(calls[0]["fit_score"], float)
    assert isinstance(calls[0]["ranking_score"], float)
    assert isinstance(calls[0]["score_components"], dict)
    assert calls[0]["source_profile_id"] == 7
    assert calls[0]["job_description_hash"] == job_description_hash("Python services")
