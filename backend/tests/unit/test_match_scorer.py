"""Unit coverage for the Phase 3A pure deterministic scorer."""

from __future__ import annotations

from typing import cast

from app.domain.matching.scorer import (
    SCORER_VERSION,
    CanonicalSkill,
    DeterministicScoreInput,
    DeterministicScoreResult,
    ReadinessFindings,
    score_match,
)


def skill(skill_id: str, label: str | None = None) -> CanonicalSkill:
    return CanonicalSkill(skill_id, label)


def components(result: DeterministicScoreResult) -> dict[str, dict[str, object]]:
    return cast(dict[str, dict[str, object]], result.score_components["components"])


def component(result: DeterministicScoreResult, name: str) -> dict[str, object]:
    return components(result)[name]


def score(result: DeterministicScoreResult, name: str) -> float:
    return cast(float, component(result, name)["score"])


def test_weights_and_weighted_aggregation_are_explicit() -> None:
    result = score_match(
        DeterministicScoreInput(
            required_skills=(skill("python"),),
            matched_required_skills=(skill("python", "Python"),),
            preferred_skills=(skill("aws"),),
            matched_preferred_skills=(skill("aws"),),
            semantic_similarity=1.0,
            job_title="Backend Engineer",
            profile_roles=("Backend Engineer",),
            job_seniority="senior",
            profile_seniority="senior",
            job_years_required=5,
            profile_years_experience=5,
            readiness=ReadinessFindings(),
        )
    )
    assert result.fit_score == 100
    assert result.ranking_score == 100
    assert result.score_components["version"] == SCORER_VERSION
    assert result.score_components["weights"] == {
        "required_skill_coverage": 35,
        "preferred_skill_coverage": 10,
        "semantic_similarity": 25,
        "role_alignment": 15,
        "seniority_experience": 10,
        "user_preferences": 5,
    }


def test_fit_score_is_bounded_and_semantic_input_is_converted() -> None:
    result = score_match(DeterministicScoreInput(semantic_similarity=2.0))
    assert result.fit_score == 62.5
    assert component(result, "semantic_similarity")["score"] == 100


def test_required_preferred_and_unclassified_evidence_are_independent() -> None:
    result = score_match(
        DeterministicScoreInput(
            required_skills=(skill("python"), skill("aws")),
            matched_required_skills=(skill("python"),),
            preferred_skills=(skill("docker"),),
            matched_preferred_skills=(),
        )
    )
    score_components = components(result)
    assert score_components["required_skill_coverage"]["score"] == 50
    assert score_components["preferred_skill_coverage"]["score"] == 0
    assert "unclassified" not in score_components


def test_no_preferred_requirements_is_neutral_unknown() -> None:
    result = score_match(DeterministicScoreInput(preferred_skills=()))
    preferred = component(result, "preferred_skill_coverage")
    assert preferred == {
        "score": 50.0,
        "weight": 10,
        "status": "unknown",
        "reason": "no_preferred_requirements",
    }


def test_missing_required_skills_reduce_fit_without_becoming_a_blocker() -> None:
    result = score_match(
        DeterministicScoreInput(
            required_skills=(skill("python"), skill("kubernetes")),
            matched_required_skills=(skill("python"),),
            readiness=ReadinessFindings(),
        )
    )
    assert result.ranking_score == result.fit_score
    assert result.fit_score < 100


def test_blocker_zeroes_ranking_but_preserves_fit() -> None:
    result = score_match(
        DeterministicScoreInput(
            semantic_similarity=0.8,
            readiness=ReadinessFindings(blockers=("salary",)),
        )
    )
    assert result.fit_score > 0
    assert result.ranking_score == 0


def test_warning_only_changes_preference_component() -> None:
    base = score_match(DeterministicScoreInput(readiness=ReadinessFindings()))
    warned = score_match(
        DeterministicScoreInput(readiness=ReadinessFindings(warnings=("location",)))
    )
    base_components = components(base)
    warned_components = components(warned)
    assert (
        score(warned, "user_preferences") < score(base, "user_preferences")
    )
    for name in base_components:
        if name != "user_preferences":
            assert warned_components[name] == base_components[name]


def test_all_unavailable_components_are_neutral_unknown() -> None:
    score_components = components(score_match(DeterministicScoreInput()))
    assert all(
        item["score"] == 50 and item["status"] == "unknown"
        for item in score_components.values()
    )
    assert all(
        isinstance(item["reason"], str) and item["reason"]
        for item in score_components.values()
    )


def test_role_exact_and_partial_token_f1() -> None:
    exact = score_match(
        DeterministicScoreInput(
            job_title="Senior Backend Engineer",
            profile_roles=("Senior Backend Engineer",),
        )
    )
    partial = score_match(
        DeterministicScoreInput(job_title="Backend Engineer", profile_roles=("Backend Architect",))
    )
    assert component(exact, "role_alignment")["score"] == 100
    assert 0 < score(partial, "role_alignment") < 100


def test_missing_role_evidence_is_unknown() -> None:
    role = score_match(
        DeterministicScoreInput(job_title="Engineer")
    )
    role_component = component(role, "role_alignment")
    assert role_component["score"] == 50 and role_component["status"] == "unknown"


def test_supported_seniority_and_years_comparison() -> None:
    result = score_match(
        DeterministicScoreInput(
            job_seniority="senior",
            profile_seniority="lead",
            job_years_required=5,
            profile_years_experience=7,
        )
    )
    assert component(result, "seniority_experience")["score"] == 100


def test_ambiguous_or_one_sided_experience_is_unknown() -> None:
    ambiguous = score_match(
        DeterministicScoreInput(job_seniority="expert", profile_seniority="senior")
    )
    one_sided = score_match(DeterministicScoreInput(job_years_required=5))
    for result in (ambiguous, one_sided):
        experience = component(result, "seniority_experience")
        assert experience["score"] == 50 and experience["status"] == "unknown"


def test_repeated_inputs_are_deterministic_and_no_llm_input_exists() -> None:
    data = DeterministicScoreInput(
        required_skills=(skill("python"),), matched_required_skills=(skill("python"),)
    )
    assert score_match(data) == score_match(data)
    assert not hasattr(data, "llm_score")


def test_skill_identity_uses_ids_not_labels_and_deduplicates() -> None:
    result = score_match(
        DeterministicScoreInput(
            required_skills=(
                skill("id-a", "Cloud"),
                skill("id-a", "Cloud"),
                skill("id-b", "Cloud"),
            ),
            matched_required_skills=(skill("id-a", "Different label"), skill("id-a", "Cloud")),
        )
    )
    assert component(result, "required_skill_coverage")["score"] == 50


def test_required_preferred_and_unclassified_ids_are_independent() -> None:
    result = score_match(
        DeterministicScoreInput(
            required_skills=(skill("required"),),
            matched_required_skills=(),
            preferred_skills=(skill("preferred"),),
            matched_preferred_skills=(),
        )
    )
    score_components = components(result)
    assert score_components["required_skill_coverage"]["score"] == 0
    assert score_components["preferred_skill_coverage"]["score"] == 0


def test_experience_conflicts_are_neutral_and_consistent_signals_combine() -> None:
    aligned = score_match(
        DeterministicScoreInput(
            job_seniority="senior",
            profile_seniority="senior",
            job_years_required=5,
            profile_years_experience=7,
        )
    )
    conflicting = score_match(
        DeterministicScoreInput(
            job_seniority="senior",
            profile_seniority="junior",
            job_years_required=5,
            profile_years_experience=7,
        )
    )
    assert (
        component(aligned, "seniority_experience")["reason"]
        == "seniority_and_years_alignment"
    )
    assert component(conflicting, "seniority_experience") == {
        "score": 50.0,
        "weight": 10,
        "status": "unknown",
        "reason": "conflicting_experience_evidence",
    }


def test_non_finite_semantic_values_are_unknown() -> None:
    import math

    for value in (math.nan, math.inf, -math.inf):
        result = score_match(DeterministicScoreInput(semantic_similarity=value))
        assert component(result, "semantic_similarity") == {
            "score": 50.0,
            "weight": 25,
            "status": "unknown",
            "reason": "non_finite_semantic_input",
        }


def test_generic_role_overlap_is_unknown_but_meaningful_overlap_scores() -> None:
    generic = score_match(
        DeterministicScoreInput(
            job_title="Backend Engineer", profile_roles=("Mechanical Engineer",)
        )
    )
    meaningful = score_match(
        DeterministicScoreInput(
            job_title="Senior Python Developer", profile_roles=("Python Developer",)
        )
    )
    assert (
        component(generic, "role_alignment")["reason"]
        == "generic_role_overlap_only"
    )
    assert score(meaningful, "role_alignment") > 0


def test_preferred_coverage_reasons_reflect_full_partial_and_zero() -> None:
    for matched, expected in [
        ((skill("a"), skill("b")), "matched_preferred_coverage"),
        ((skill("a"),), "missing_preferred_coverage"),
        ((), "missing_preferred_coverage"),
    ]:
        result = score_match(DeterministicScoreInput(
            preferred_skills=(skill("a"), skill("b")),
            matched_preferred_skills=matched,
        ))
        assert component(result, "preferred_skill_coverage")["reason"] == expected


def test_consistent_underqualification_is_known_and_multiple_roles_use_maximum() -> None:
    experience_result = score_match(DeterministicScoreInput(
        job_seniority="senior", profile_seniority="junior",
        job_years_required=10, profile_years_experience=2,
    ))
    experience = component(experience_result, "seniority_experience")
    assert experience["status"] == "known"
    assert experience["reason"] == "seniority_and_years_alignment"
    roles_result = score_match(DeterministicScoreInput(
        job_title="Python Backend Engineer",
        profile_roles=("Unrelated Role", "Python Backend Engineer"),
    ))
    roles = component(roles_result, "role_alignment")
    assert roles["score"] == 100


def test_score_result_serializes_to_finite_json() -> None:
    import json
    encoded = json.dumps(
        score_match(DeterministicScoreInput(semantic_similarity=1.0)).score_components,
        allow_nan=False,
    )
    assert "NaN" not in encoded and "Infinity" not in encoded
