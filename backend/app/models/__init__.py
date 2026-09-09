"""Model registry — import all models so Alembic autogenerate sees them."""

from __future__ import annotations

from app.models.application import Application
from app.models.application_event import ApplicationEvent
from app.models.application_form_field import ApplicationFormField
from app.models.application_form_session import ApplicationFormSession
from app.models.application_package import ApplicationPackage
from app.models.application_preparation import ApplicationPreparation
from app.models.cover_letter import CoverLetter
from app.models.extension_refresh_token import ExtensionRefreshToken
from app.models.extension_session import ExtensionSession
from app.models.interview_prep import InterviewPrep
from app.models.job import Job
from app.models.job_source_link import JobSourceLink
from app.models.resume import CandidateProfile, Resume, TailoredResumeDraft
from app.models.scrape_query import ScrapeQuery
from app.models.scrape_query_source_state import ScrapeQuerySourceState
from app.models.user import User
from app.models.user_job import UserJob
from app.models.user_job_import import UserJobImport
from app.models.user_job_match import UserJobMatch
from app.models.user_job_preference import UserJobPreference
from app.models.user_readiness_preference import UserReadinessPreference
from app.models.user_scrape_query_subscription import UserScrapeQuerySubscription
from app.models.web_refresh_session import WebRefreshSession

__all__ = [
    "Application",
    "ApplicationEvent",
    "ApplicationPackage",
    "ApplicationPreparation",
    "ApplicationFormField",
    "ApplicationFormSession",
    "CandidateProfile",
    "CoverLetter",
    "ExtensionSession",
    "ExtensionRefreshToken",
    "InterviewPrep",
    "Job",
    "JobSourceLink",
    "Resume",
    "TailoredResumeDraft",
    "ScrapeQuery",
    "ScrapeQuerySourceState",
    "User",
    "UserJob",
    "UserJobImport",
    "UserJobMatch",
    "UserJobPreference",
    "UserReadinessPreference",
    "UserScrapeQuerySubscription",
    "WebRefreshSession",
]
