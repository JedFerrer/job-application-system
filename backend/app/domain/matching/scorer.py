"""Pure deterministic fit scoring primitives for Phase 3A."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Literal

SCORER_VERSION = "fit-scorer-v1"
ComponentStatus = Literal["known", "unknown"]
ReasonCode = Literal[
    "no_required_requirements", "matched_required_coverage", "missing_required_coverage",
    "no_preferred_requirements", "matched_preferred_coverage", "missing_preferred_coverage",
    "missing_semantic_evidence", "non_finite_semantic_input", "semantic_similarity_available",
    "exact_role_match", "partial_role_alignment", "generic_role_overlap_only",
    "missing_role_evidence",
    "seniority_alignment", "years_alignment", "seniority_and_years_alignment",
    "conflicting_experience_evidence", "missing_experience_evidence", "no_preference_findings",
    "confirmed_preference_blocker", "preference_warnings", "preference_findings_clear",
]

WEIGHTS: dict[str, int] = {
    "required_skill_coverage": 35,
    "preferred_skill_coverage": 10,
    "semantic_similarity": 25,
    "role_alignment": 15,
    "seniority_experience": 10,
    "user_preferences": 5,
}


@dataclass(frozen=True)
class CanonicalSkill:
    """Canonical skill identity with optional display-only metadata."""

    skill_id: str
    label: str | None = None


@dataclass(frozen=True)
class ReadinessFindings:
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScoreComponent:
    score: float
    weight: int
    status: ComponentStatus
    reason: ReasonCode

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class DeterministicScoreInput:
    required_skills: tuple[CanonicalSkill, ...] = ()
    matched_required_skills: tuple[CanonicalSkill, ...] = ()
    preferred_skills: tuple[CanonicalSkill, ...] = ()
    matched_preferred_skills: tuple[CanonicalSkill, ...] = ()
    semantic_similarity: float | None = None
    job_title: str | None = None
    profile_roles: tuple[str, ...] = ()
    profile_headline: str | None = None
    job_seniority: str | None = None
    profile_seniority: str | None = None
    job_years_required: float | None = None
    profile_years_experience: float | None = None
    readiness: ReadinessFindings | None = None


@dataclass(frozen=True)
class DeterministicScoreResult:
    fit_score: float
    ranking_score: float
    score_components: dict[str, object] = field(default_factory=dict)
    scorer_version: str = SCORER_VERSION


_TOKEN_RE = re.compile(r"[\w+#.]+", re.UNICODE)
_GENERIC_ROLE_TOKENS = {
    "engineer",
    "developer",
    "specialist",
    "manager",
    "analyst",
    "lead",
    "senior",
    "junior",
}
_SENIORITY = {
    "intern": 0,
    "trainee": 0,
    "junior": 1,
    "entry": 1,
    "associate": 2,
    "mid": 2,
    "mid-level": 2,
    "intermediate": 2,
    "senior": 3,
    "lead": 4,
    "principal": 5,
    "staff": 5,
    "director": 6,
    "vp": 7,
    "executive": 7,
}


def _bounded(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _coverage(
    total: tuple[CanonicalSkill, ...],
    matched: tuple[CanonicalSkill, ...],
    group: Literal["required", "preferred"],
) -> tuple[float, ComponentStatus, ReasonCode]:
    total_ids = {item.skill_id for item in total if item.skill_id}
    matched_ids = {item.skill_id for item in matched if item.skill_id}
    if not total_ids:
        empty_reason: ReasonCode = (
            "no_required_requirements" if group == "required" else "no_preferred_requirements"
        )
        return 50.0, "unknown", empty_reason
    reason: ReasonCode = (
        "matched_required_coverage" if group == "required" else "matched_preferred_coverage"
    ) if total_ids <= matched_ids else (
        "missing_required_coverage" if group == "required" else "missing_preferred_coverage"
    )
    return (
        _bounded(len(total_ids & matched_ids) / len(total_ids) * 100),
        "known",
        reason,
    )


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(value) if token.strip()}


def _f1(left: set[str], right: set[str]) -> float | None:
    if not left or not right:
        return None
    overlap = len(left & right)
    if overlap == 0:
        return 0.0
    precision = overlap / len(left)
    recall = overlap / len(right)
    return 2 * precision * recall / (precision + recall)


def _role_alignment(data: DeterministicScoreInput) -> tuple[float, ComponentStatus, ReasonCode]:
    title = _tokens(data.job_title or "")
    candidates = [_tokens(role) for role in (*data.profile_roles, data.profile_headline or "")]
    candidates = [candidate for candidate in candidates if candidate]
    if not title or not candidates:
        return 50.0, "unknown", "missing_role_evidence"
    normalized_title = " ".join(sorted(title))
    for candidate in candidates:
        if " ".join(sorted(candidate)) == normalized_title:
            return 100.0, "known", "exact_role_match"
    meaningful_title = title - _GENERIC_ROLE_TOKENS
    scores: list[float] = []
    generic_only = True
    for candidate in candidates:
        meaningful_candidate = candidate - _GENERIC_ROLE_TOKENS
        overlap = meaningful_title & meaningful_candidate
        if overlap:
            generic_only = False
        score = _f1(meaningful_title, meaningful_candidate)
        if score is not None:
            scores.append(score)
    if generic_only:
        return 50.0, "unknown", "generic_role_overlap_only"
    return _bounded(max(scores or [0.0]) * 100), "known", "partial_role_alignment"


def _experience(data: DeterministicScoreInput) -> tuple[float, ComponentStatus, ReasonCode]:
    seniority: tuple[float, bool] | None = None
    if data.job_seniority and data.profile_seniority:
        required = _SENIORITY.get(data.job_seniority.casefold().strip())
        profile = _SENIORITY.get(data.profile_seniority.casefold().strip())
        if required is not None and profile is not None:
            seniority = (
                100.0 if profile >= required else _bounded(profile / max(required, 1) * 100),
                profile >= required,
            )

    years: tuple[float, bool] | None = None
    if data.job_years_required is not None and data.profile_years_experience is not None:
        if data.job_years_required >= 0 and data.profile_years_experience >= 0:
            years = (
                100.0
                if data.job_years_required == 0
                else _bounded(data.profile_years_experience / data.job_years_required * 100),
                data.profile_years_experience >= data.job_years_required,
            )

    if seniority is not None and years is not None:
        if seniority[1] != years[1]:
            return 50.0, "unknown", "conflicting_experience_evidence"
        return _bounded((seniority[0] + years[0]) / 2), "known", "seniority_and_years_alignment"
    if seniority is not None:
        return seniority[0], "known", "seniority_alignment"
    if years is not None:
        return years[0], "known", "years_alignment"
    return 50.0, "unknown", "missing_experience_evidence"


def _preferences(findings: ReadinessFindings | None) -> tuple[float, ComponentStatus, ReasonCode]:
    if findings is None:
        return 50.0, "unknown", "no_preference_findings"
    if findings.blockers:
        return 0.0, "known", "confirmed_preference_blocker"
    if findings.warnings:
        return _bounded(100.0 - 25.0 * len(findings.warnings)), "known", "preference_warnings"
    return 100.0, "known", "preference_findings_clear"


def score_match(data: DeterministicScoreInput) -> DeterministicScoreResult:
    required_score, required_status, required_reason = _coverage(
        data.required_skills, data.matched_required_skills, "required"
    )
    preferred_score, preferred_status, preferred_reason = _coverage(
        data.preferred_skills, data.matched_preferred_skills, "preferred"
    )

    semantic_status: ComponentStatus
    semantic_reason: ReasonCode
    if data.semantic_similarity is None:
        semantic_score, semantic_status, semantic_reason = (
            50.0,
            "unknown",
            "missing_semantic_evidence",
        )
    elif not math.isfinite(data.semantic_similarity):
        semantic_score, semantic_status, semantic_reason = (
            50.0,
            "unknown",
            "non_finite_semantic_input",
        )
    else:
        semantic_score, semantic_status, semantic_reason = (
            _bounded(data.semantic_similarity * 100),
            "known",
            "semantic_similarity_available",
        )

    role_score, role_status, role_reason = _role_alignment(data)
    experience_score, experience_status, experience_reason = _experience(data)
    preference_score, preference_status, preference_reason = _preferences(data.readiness)
    values = {
        "required_skill_coverage": ScoreComponent(
            required_score, 35, required_status, required_reason
        ),
        "preferred_skill_coverage": ScoreComponent(
            preferred_score, 10, preferred_status, preferred_reason
        ),
        "semantic_similarity": ScoreComponent(
            semantic_score, 25, semantic_status, semantic_reason
        ),
        "role_alignment": ScoreComponent(role_score, 15, role_status, role_reason),
        "seniority_experience": ScoreComponent(
            experience_score, 10, experience_status, experience_reason
        ),
        "user_preferences": ScoreComponent(
            preference_score, 5, preference_status, preference_reason
        ),
    }
    fit_score = _bounded(sum(item.score * item.weight for item in values.values()) / 100)
    ranking_score = 0.0 if data.readiness and data.readiness.blockers else fit_score
    return DeterministicScoreResult(
        fit_score=fit_score,
        ranking_score=ranking_score,
        score_components={
            "version": SCORER_VERSION,
            "weights": dict(WEIGHTS),
            "components": {name: item.as_dict() for name, item in values.items()},
        },
    )
