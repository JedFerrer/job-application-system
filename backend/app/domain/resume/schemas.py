"""Resume domain schemas.

``ParsedProfile`` is the structured contract the LLM must return when parsing
a resume, and is also the shape persisted in ``candidate_profiles.parsed``.
Ported/adapted from hr-ai-nterviewer-saas (``schemas/resume.py``): we keep the
``year`` int->str coercion and ``AliasChoices`` tolerance because LLMs drift on
field names and return numeric years.
"""

from __future__ import annotations

import re

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


class WorkItem(BaseModel):
    """A single work-history entry."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    company: str | None = None
    title: str | None = Field(
        default=None,
        validation_alias=AliasChoices("title", "role"),
    )
    start: str | None = None
    end: str | None = None
    highlights: list[str] = Field(default_factory=list)


class EducationItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    degree: str | None = None
    institution: str | None = None
    year: str | None = None

    @field_validator("year", mode="before")
    @classmethod
    def _coerce_year(cls, value: object) -> object:
        if value is None or isinstance(value, str):
            return value
        return str(value)


class ParsedProfile(BaseModel):
    """Normalized, validated resume payload (also the LLM output contract)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    full_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("full_name", "name"),
    )
    email: str | None = None
    phone: str | None = None
    headline: str | None = Field(
        default=None,
        validation_alias=AliasChoices("headline", "current_role", "summary"),
    )
    years_experience: float | None = None
    skills: list[str] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    work_history: list[WorkItem] = Field(
        default_factory=list,
        validation_alias=AliasChoices("work_history", "experience"),
    )
    preferred_roles: list[str] = Field(default_factory=list)
    # entry | mid | senior | staff | lead
    seniority: str | None = None

    @field_validator("years_experience", mode="before")
    @classmethod
    def _coerce_years(cls, value: object) -> object:
        if value is None or isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            match = re.search(r"\d+(?:\.\d+)?", value)
            return float(match.group()) if match else None
        return value


class ResumeResponse(BaseModel):
    """API response after a resume is uploaded and parsed."""

    model_config = ConfigDict(from_attributes=True)

    resume_id: int
    filename: str
    is_active: bool
    profile: ParsedProfile


class ProfileResponse(BaseModel):
    """API response for the active candidate profile."""

    model_config = ConfigDict(from_attributes=True)

    profile_id: int
    resume_id: int
    profile: ParsedProfile


class ProfileUpdate(BaseModel):
    """Editable profile fields (user can correct LLM mistakes)."""

    model_config = ConfigDict(extra="forbid")

    profile: ParsedProfile
