"""Resume domain schemas.

``ParsedProfile`` is the structured contract the LLM must return when parsing
a resume, and is also the shape persisted in ``candidate_profiles.parsed``.
Ported/adapted from hr-ai-nterviewer-saas (``schemas/resume.py``): we keep the
``year`` int->str coercion and ``AliasChoices`` tolerance because LLMs drift on
field names and return numeric years.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.domain.resume.lifecycle import ResumeLifecycleStatus


def _normalize_optional_text(value: object) -> object:
    """Trim optional extracted text and normalize blanks to ``None``."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _normalize_list_text(value: object) -> object:
    """Trim non-empty strings in extracted list fields."""
    if value is None:
        return []
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return value


def _validate_profile_url(value: object) -> object:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return None
    if not isinstance(normalized, str):
        return normalized
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must use the http:// or https:// scheme")
    return normalized


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
    highlights: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "highlights",
            "bullets",
            "achievements",
            "responsibilities",
            "details",
        ),
    )


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


class ProfessionalLinks(BaseModel):
    """Optional professional links extracted from a resume."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    linkedin: str | None = Field(default=None, max_length=2048)
    github: str | None = Field(default=None, max_length=2048)
    portfolio: str | None = Field(default=None, max_length=2048)
    website: str | None = Field(default=None, max_length=2048)

    @field_validator("linkedin", "github", "portfolio", "website", mode="before")
    @classmethod
    def _normalize_url(cls, value: object) -> object:
        return _validate_profile_url(value)


class ProjectItem(BaseModel):
    """A project listed in the candidate's resume."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    technologies: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    url: str | None = Field(default=None, max_length=2048)

    @field_validator("name", "description", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> object:
        return _normalize_optional_text(value)

    @field_validator("technologies", "achievements", mode="before")
    @classmethod
    def _normalize_text_list(cls, value: object) -> object:
        return _normalize_list_text(value)

    @field_validator("url", mode="before")
    @classmethod
    def _normalize_url(cls, value: object) -> object:
        return _validate_profile_url(value)


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
    links: ProfessionalLinks = Field(default_factory=ProfessionalLinks)
    projects: list[ProjectItem] = Field(default_factory=list)
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
    lifecycle_status: ResumeLifecycleStatus = ResumeLifecycleStatus.ACTIVE
    profile: ParsedProfile
    matching_profile_changed: bool = True


class ProfileResponse(BaseModel):
    """API response for the active candidate profile."""

    model_config = ConfigDict(from_attributes=True)

    profile_id: int
    resume_id: int
    profile: ParsedProfile
    matching_profile_changed: bool = False


class ProfileUpdate(BaseModel):
    """Editable profile fields (user can correct LLM mistakes)."""

    model_config = ConfigDict(extra="forbid")

    profile: ParsedProfile


# ---- Resume tailoring (Phase 2) ---------------------------------------------


class TailoredWorkItem(BaseModel):
    """A single work-history entry after tailoring for a specific job."""

    company: str | None = None
    title: str | None = None
    start: str | None = None
    end: str | None = None
    original_highlights: list[str] = Field(default_factory=list)
    tailored_highlights: list[str] = Field(default_factory=list)


class TailoredResume(BaseModel):
    """A profile rewritten to emphasize relevance for a specific job posting."""

    job_id: int
    professional_summary: str | None = None
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    headline: str | None = None
    work_history: list[TailoredWorkItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    matched_skills: list[str] = Field(default_factory=list)
    other_skills: list[str] = Field(default_factory=list)
    missing_keywords: list[str] = Field(default_factory=list)


class TailoredResumeDraftCreate(BaseModel):
    job_id: int
    content: TailoredResume

    @model_validator(mode="after")
    def _content_matches_job(self) -> TailoredResumeDraftCreate:
        if self.content.job_id != self.job_id:
            raise ValueError("content.job_id must match job_id")
        return self


class TailoredResumeDraftListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int | None
    source_profile_id: int | None
    job_title: str
    job_company: str | None
    created_at: datetime
    updated_at: datetime


class TailoredResumeDraftResponse(TailoredResumeDraftListItem):
    content: TailoredResume
