"""Model registry — import all models so Alembic autogenerate sees them."""

from __future__ import annotations

from app.models.application import Application
from app.models.cover_letter import CoverLetter
from app.models.job import Job
from app.models.resume import CandidateProfile, Resume
from app.models.user import User
from app.models.user_job_match import UserJobMatch

__all__ = [
    "Application",
    "CandidateProfile",
    "CoverLetter",
    "Job",
    "Resume",
    "User",
    "UserJobMatch",
]
