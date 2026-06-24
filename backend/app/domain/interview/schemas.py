"""Interview prep API schemas (Phase 2)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

QuestionCategory = Literal[
    "technical",
    "behavioral",
    "system_design",
    "role_specific",
    "culture_fit",
]


class InterviewPrepRequest(BaseModel):
    """Body for ``POST /interview-prep/{job_id}``."""

    model_config = ConfigDict(str_strip_whitespace=True)

    focus_areas: list[str] = Field(default_factory=list, max_length=10)
    question_count: int = Field(default=8, ge=3, le=20)


class InterviewQuestion(BaseModel):
    question: str
    category: QuestionCategory
    why_it_matters: str
    suggested_angle: str


class InterviewPrepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: int
    questions: list[InterviewQuestion]
    focus_areas: list[str]
    model: str | None
    cached: bool = False
    created_at: datetime
