"""Model registry — import all models so Alembic autogenerate sees them."""

from __future__ import annotations

from app.models.application import Application
from app.models.application_event import ApplicationEvent
from app.models.application_form_field import ApplicationFormField
from app.models.application_form_session import ApplicationFormSession
from app.models.cover_letter import CoverLetter
from app.models.extension_session import ExtensionSession
from app.models.interview_prep import InterviewPrep
from app.models.job import Job
from app.models.job_source_link import JobSourceLink
from app.models.resume import CandidateProfile, Resume
from app.models.user import User
from app.models.user_job import UserJob
from app.models.user_job_import import UserJobImport
from app.models.user_job_match import UserJobMatch
from app.models.user_job_preference import UserJobPreference

__all__ = [
    "Application",
    "ApplicationEvent",
    "ApplicationFormField",
    "ApplicationFormSession",
    "CandidateProfile",
    "CoverLetter",
    "ExtensionSession",
    "InterviewPrep",
    "Job",
    "JobSourceLink",
    "Resume",
    "User",
    "UserJob",
    "UserJobImport",
    "UserJobMatch",
    "UserJobPreference",
]
