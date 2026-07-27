"""Phase 5A ranking rollout settings and mode boundary tests."""

from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import ValidationError

from app.core.config import Settings

pytestmark = pytest.mark.unit


def test_ranking_settings_default_to_safe_shadow_configuration() -> None:
    settings = Settings()
    assert settings.match_ranking_mode == "shadow"
    assert settings.match_vector_candidate_limit == 400
    assert settings.match_text_candidate_limit == 400
    assert settings.match_skill_candidate_limit == 400
    assert settings.match_final_limit == 200
    assert settings.match_rrf_k == 60
    assert settings.match_scorer_version == "fit-scorer-v1"


@pytest.mark.parametrize("mode", ["vector", "shadow", "fit"])
def test_ranking_mode_accepts_all_rollout_values(mode: str) -> None:
    assert Settings(match_ranking_mode=mode).match_ranking_mode == mode


def test_ranking_mode_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        Settings(match_ranking_mode=cast(Any, "unknown"))


def test_ranking_settings_parse_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATCH_RANKING_MODE", "fit")
    monkeypatch.setenv("MATCH_VECTOR_CANDIDATE_LIMIT", "17")
    monkeypatch.setenv("MATCH_TEXT_CANDIDATE_LIMIT", "18")
    monkeypatch.setenv("MATCH_SKILL_CANDIDATE_LIMIT", "19")
    monkeypatch.setenv("MATCH_FINAL_LIMIT", "20")
    monkeypatch.setenv("MATCH_RRF_K", "61")
    monkeypatch.setenv("MATCH_SCORER_VERSION", "fit-scorer-test")

    settings = Settings()
    assert settings.match_ranking_mode == "fit"
    assert settings.match_vector_candidate_limit == 17
    assert settings.match_text_candidate_limit == 18
    assert settings.match_skill_candidate_limit == 19
    assert settings.match_final_limit == 20
    assert settings.match_rrf_k == 61
    assert settings.match_scorer_version == "fit-scorer-test"


@pytest.mark.parametrize(
    "field",
    [
        "match_vector_candidate_limit",
        "match_text_candidate_limit",
        "match_skill_candidate_limit",
        "match_final_limit",
        "match_rrf_k",
    ],
)
@pytest.mark.parametrize("value", [0, -1])
def test_ranking_limits_must_be_positive(field: str, value: int) -> None:
    def build_settings() -> Settings:
        if field == "match_vector_candidate_limit":
            return Settings(match_vector_candidate_limit=value)
        if field == "match_text_candidate_limit":
            return Settings(match_text_candidate_limit=value)
        if field == "match_skill_candidate_limit":
            return Settings(match_skill_candidate_limit=value)
        if field == "match_final_limit":
            return Settings(match_final_limit=value)
        if field == "match_rrf_k":
            return Settings(match_rrf_k=value)
        raise AssertionError(f"Unhandled settings field: {field}")

    with pytest.raises(ValidationError):
        build_settings()
