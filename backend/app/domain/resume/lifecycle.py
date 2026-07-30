"""Stable lifecycle values for imported resumes."""

from enum import StrEnum


class ResumeLifecycleStatus(StrEnum):
    ACTIVE = "active"
    DRAFT = "draft"
    INACTIVE = "inactive"
