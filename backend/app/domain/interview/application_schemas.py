"""Application-scoped interview preparation contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

InterviewStage = Literal["screening", "technical", "behavioral", "final", "unspecified"]
SourceLabel = Literal["confirmed_submission", "current_profile_fallback"]


class ApplicationInterviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: int | None = Field(default=None, gt=0)
    current_profile_fallback: bool = False
    stage: InterviewStage = "unspecified"
    focus_areas: list[str] = Field(default_factory=list, max_length=10)
    question_count: int = Field(default=8, ge=3, le=20)
    force_refresh: bool = False

    @field_validator("focus_areas")
    @classmethod
    def normalize_focus(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 200 for value in normalized):
            raise ValueError("Focus areas must contain 1 to 200 characters")
        return sorted(set(normalized), key=lambda value: (value.casefold(), value))


class ApplicationInterviewQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=1000)
    suggested_answer: str = Field(min_length=1, max_length=3000)
    source_locators: list[str] = Field(min_length=1, max_length=10)


class ApplicationInterviewEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locator: str
    source: Literal["job", "resume", "cover_letter", "manual_questions", "profile"]


class ApplicationInterviewResponse(BaseModel):
    application_id: int
    submission_id: int | None
    source_label: SourceLabel
    stage: InterviewStage
    focus_areas: list[str]
    question_count: int
    context_hash: str
    cached: bool
    questions: list[ApplicationInterviewQuestion]
    evidence: list[ApplicationInterviewEvidence]
    model: str | None
    created_at: datetime
    updated_at: datetime
