"""Model registry — import all models so Alembic autogenerate sees them."""

from __future__ import annotations

from app.models.application import Application
from app.models.cover_letter import CoverLetter
from app.models.interview_prep import InterviewPrep
from app.models.job import Job
from app.models.resume import CandidateProfile, Resume
from app.models.user import User
from app.models.user_job import UserJob
from app.models.user_job_match import UserJobMatch
from app.models.user_job_preference import UserJobPreference

__all__ = [
    "Application",
    "CandidateProfile",
    "CoverLetter",
    "InterviewPrep",
    "Job",
    "Resume",
    "User",
    "UserJob",
    "UserJobMatch",
    "UserJobPreference",
]
