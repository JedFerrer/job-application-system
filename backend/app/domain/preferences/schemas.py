"""Preferences API schemas (Phase 2)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RemotePreference = Literal["remote", "onsite", "hybrid", "any"]


class PreferenceBase(BaseModel):
    """Shared, user-editable preference fields."""

    model_config = ConfigDict(str_strip_whitespace=True)

    job_titles: list[str] = Field(default_factory=list, max_length=20)
    keywords: list[str] = Field(default_factory=list, max_length=50)
    excluded_keywords: list[str] = Field(default_factory=list, max_length=50)
    locations: list[str] = Field(default_factory=list, max_length=20)
    job_types: list[str] = Field(default_factory=list, max_length=10)
    enabled_sources: list[str] = Field(default_factory=list, max_length=10)
    remote_preference: RemotePreference | None = None
    salary_min: int | None = Field(default=None, ge=0)
    results_per_source: int = Field(default=50, ge=1, le=200)
    # Max age of postings in hours. 168 = 1 week (sensible default).
    hours_old: int = Field(default=168, ge=1, le=8760)
    is_active: bool = True


class PreferenceCreate(PreferenceBase):
    """Body for ``POST /preferences`` (full upsert)."""


class PreferenceUpdate(BaseModel):
    """Body for ``PATCH /preferences`` (partial update). All fields optional."""

    model_config = ConfigDict(str_strip_whitespace=True)

    job_titles: list[str] | None = Field(default=None, max_length=20)
    keywords: list[str] | None = Field(default=None, max_length=50)
    excluded_keywords: list[str] | None = Field(default=None, max_length=50)
    locations: list[str] | None = Field(default=None, max_length=20)
    job_types: list[str] | None = Field(default=None, max_length=10)
    enabled_sources: list[str] | None = Field(default=None, max_length=10)
    remote_preference: RemotePreference | None = None
    salary_min: int | None = Field(default=None, ge=0)
    results_per_source: int | None = Field(default=None, ge=1, le=200)
    hours_old: int | None = Field(default=None, ge=1, le=8760)
    is_active: bool | None = None


class PreferenceResponse(PreferenceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    last_scraped_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RefreshResponse(BaseModel):
    """Returned by ``POST /preferences/refresh`` when a scrape is dispatched."""

    task_id: str
    dispatched_at: datetime
