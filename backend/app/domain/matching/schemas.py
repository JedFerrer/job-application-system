"""Matching API schemas (Phase 3)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RankRequest(BaseModel):
    """Body for ``POST /matches/rank``."""

    limit: int = Field(default=200, ge=1, le=500)


class RankResponse(BaseModel):
    ranked: int
    profile_id: int


class RankTaskResponse(BaseModel):
    """Accepted or coalesced user-scoped match-ranking request."""

    task_id: str | None
    state: Literal["queued", "active", "cooldown"]
    retry_after_seconds: int | None = None


class MatchReadiness(BaseModel):
    """Deterministic application-readiness summary for a match."""

    model_config = ConfigDict(from_attributes=True)

    decision: Literal["apply", "save", "stretch", "skip"]
    reasons: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    deterministic_blockers: list[str] = Field(default_factory=list)
    deterministic_warnings: list[str] = Field(default_factory=list)
    decision_basis: Literal["score", "blocker"] = "score"


class MatchItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: int
    title: str
    company: str | None
    location: str | None
    is_remote: bool | None
    job_type: str | None = None
    description: str | None = None
    shift_type: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    source: str
    source_url: str
    company_logo: str | None = None
    vector_score: float
    confidence: str | None
    llm_score: int | None
    llm_confidence: float | None = None
    matched_skills: list[str] | None = None
    readiness: MatchReadiness
    posted_at: datetime | None


class MatchListResponse(BaseModel):
    items: list[MatchItem]
    total: int
    limit: int
    offset: int
    next_cursor: str | None = None
    has_more: bool = False


class MatchExplanation(BaseModel):
    """Layer-2 (deterministic) + layer-3 (LLM) match detail for one job."""

    job_id: int
    vector_score: float
    confidence: str | None
    llm_score: int | None
    llm_confidence: float | None
    llm_available: bool
    llm_error: str | None
    explanation: str | None
    matched_skills: list[str]
    missing_keywords: list[str]
    readiness: MatchReadiness


class MatchSummary(BaseModel):
    """Persisted, user-scoped match data for restoring job detail."""

    job_id: int
    vector_score: float
    confidence: str | None
    llm_score: int | None
    llm_confidence: float | None = None
    explanation: str | None = None
    matched_skills: list[str]
    missing_keywords: list[str]
    readiness: MatchReadiness


class MatchSkillAnalysis(BaseModel):
    """Deterministic skill evidence for one job, without an LLM assessment."""

    job_id: int
    vector_score: float
    matched_skills: list[str]
    missing_keywords: list[str]
    readiness: MatchReadiness


class SkillEvidence(BaseModel):
    """One deterministic skill observation with bounded source excerpts."""

    skill: str
    job_excerpt: str
    profile_excerpt: str | None = None


class SkillEvidenceGroup(BaseModel):
    """Matched and missing known skills in one requirement classification."""

    matched: list[SkillEvidence] = Field(default_factory=list)
    missing: list[SkillEvidence] = Field(default_factory=list)


class ClassifiedSkillEvidence(BaseModel):
    required: SkillEvidenceGroup = Field(default_factory=SkillEvidenceGroup)
    preferred: SkillEvidenceGroup = Field(default_factory=SkillEvidenceGroup)
    unclassified: SkillEvidenceGroup = Field(default_factory=SkillEvidenceGroup)


class ExperienceRequirement(BaseModel):
    """A cautious assessment of one explicit years-of-experience statement."""

    years_required: float
    scope: Literal["total_experience", "skill_specific"]
    skill: str | None = None
    job_excerpt: str
    assessment: Literal["meets", "may_not_meet", "needs_review"]
    profile_years_experience: float | None = None


class MatchEvidence(BaseModel):
    """Fresh, filtered deterministic evidence for one visible job."""

    job_id: int
    skills: ClassifiedSkillEvidence = Field(default_factory=ClassifiedSkillEvidence)
    experience_requirements: list[ExperienceRequirement] = Field(default_factory=list)


class SkillGapItem(BaseModel):
    """One missing skill and how many of the user's matched jobs require it."""

    keyword: str
    frequency: int


class SkillsGapSummary(BaseModel):
    """Top recurring skill gaps across all of a user's matched jobs."""

    items: list[SkillGapItem]
    total_matches_considered: int
