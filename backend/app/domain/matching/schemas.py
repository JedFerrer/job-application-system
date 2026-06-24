"""Matching API schemas (Phase 3)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RankRequest(BaseModel):
    """Body for ``POST /matches/rank``."""

    limit: int = Field(default=100, ge=1, le=500)


class RankResponse(BaseModel):
    ranked: int
    profile_id: int


class MatchItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: int
    title: str
    company: str | None
    location: str | None
    is_remote: bool | None
    source: str
    source_url: str
    vector_score: float
    confidence: str | None
    llm_score: int | None
    posted_at: datetime | None


class MatchListResponse(BaseModel):
    items: list[MatchItem]
    total: int
    limit: int
    offset: int


class MatchExplanation(BaseModel):
    """Layer-2 (deterministic) + layer-3 (LLM) match detail for one job."""

    job_id: int
    vector_score: float
    confidence: str | None
    llm_score: int | None
    llm_confidence: float | None
    explanation: str | None
    matched_skills: list[str]
    missing_keywords: list[str]


class SkillGapItem(BaseModel):
    """One missing skill and how many of the user's matched jobs require it."""

    keyword: str
    frequency: int


class SkillsGapSummary(BaseModel):
    """Top recurring skill gaps across all of a user's matched jobs."""

    items: list[SkillGapItem]
    total_matches_considered: int
