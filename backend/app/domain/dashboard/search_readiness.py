"""Read-only search setup readiness for the dashboard."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domain.preferences.repository import PreferenceRepository
from app.domain.resume.repository import ResumeRepository

OverallState = Literal["needs_setup", "almost_ready", "ready"]
ResumeState = Literal["ready", "needs_import", "draft_review"]
SetupState = Literal["ready", "needs_setup"]
ReasonCode = Literal[
    "draft_review",
    "import_resume",
    "complete_profile",
    "set_job_preferences",
    "view_matches",
]


class SearchReadinessCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: ResumeState | SetupState
    has_pending_draft: bool | None = None


class SearchReadinessSetupCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: SetupState


class SearchReadinessChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume: SearchReadinessCheck
    profile: SearchReadinessSetupCheck
    preferences: SearchReadinessSetupCheck


class SearchReadinessAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: ReasonCode
    label: str


class SearchReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_state: OverallState
    checks: SearchReadinessChecks
    next_action: SearchReadinessAction


class SearchReadinessService:
    _LABELS: dict[ReasonCode, str] = {
        "draft_review": "Review resume draft",
        "import_resume": "Import resume",
        "complete_profile": "Complete profile",
        "set_job_preferences": "Set job preferences",
        "view_matches": "View matches",
    }

    def __init__(
        self,
        resume_repository: ResumeRepository,
        preference_repository: PreferenceRepository,
    ) -> None:
        self._resume_repository = resume_repository
        self._preference_repository = preference_repository

    async def get(self, user_id: int) -> SearchReadinessResponse:
        has_active_resume, usable_profile, has_draft = (
            await self._resume_repository.get_search_readiness_snapshot(user_id)
        )
        preferences_ready = await self._preference_repository.has_configured_search_titles(
            user_id
        )

        resume_state: ResumeState
        if has_active_resume:
            resume_state = "ready"
        elif has_draft:
            resume_state = "draft_review"
        else:
            resume_state = "needs_import"
        profile_state: SetupState = "ready" if usable_profile else "needs_setup"
        preferences_state: SetupState = "ready" if preferences_ready else "needs_setup"

        unresolved = (
            int(resume_state != "ready")
            + int(profile_state != "ready")
            + int(not preferences_ready)
        )
        overall: OverallState = (
            "ready" if unresolved == 0 else "almost_ready" if unresolved == 1 else "needs_setup"
        )
        reason: ReasonCode
        if not usable_profile and has_draft:
            reason = "draft_review"
        elif not has_active_resume:
            reason = "import_resume"
        elif not usable_profile:
            reason = "complete_profile"
        elif not preferences_ready:
            reason = "set_job_preferences"
        else:
            reason = "view_matches"

        return SearchReadinessResponse(
            overall_state=overall,
            checks=SearchReadinessChecks(
                resume=SearchReadinessCheck(
                    state=resume_state, has_pending_draft=has_draft
                ),
                profile=SearchReadinessSetupCheck(state=profile_state),
                preferences=SearchReadinessSetupCheck(state=preferences_state),
            ),
            next_action=SearchReadinessAction(reason=reason, label=self._LABELS[reason]),
        )
