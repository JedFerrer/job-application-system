"""Matching API schemas (Phase 3)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RankRequest(BaseModel):
    """Body for ``POST /matches/rank``."""

    limit: int = Field(default=100, ge=1, le=500)


class RankResponse(BaseModel):
    ranked: int
    profile_id: int


class MatchReadiness(BaseModel):
    """Deterministic application-readiness summary for a match."""

    model_config = ConfigDict(from_attributes=True)

    decision: Literal["apply", "save", "stretch", "skip"]
    reasons: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


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


class MatchSkillAnalysis(BaseModel):
    """Deterministic skill evidence for one job, without an LLM assessment."""

    job_id: int
    vector_score: float
    matched_skills: list[str]
    missing_keywords: list[str]
    readiness: MatchReadiness


class SkillGapItem(BaseModel):
    """One missing skill and how many of the user's matched jobs require it."""

    keyword: str
    frequency: int


class SkillsGapSummary(BaseModel):
    """Top recurring skill gaps across all of a user's matched jobs."""

    items: list[SkillGapItem]
    total_matches_considered: int
