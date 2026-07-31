"""Resume domain service: orchestrates parse -> embed -> persist.

Pure-ish business logic; no FastAPI/HTTP concerns. Raises domain exceptions.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger
from app.domain.jobs.exceptions import JobNotFoundError
from app.domain.jobs.repository import JobRepository
from app.domain.matching.skill_matcher import (
    SkillMatchReport,
    compute_skill_match,
    extract_jd_skills,
    normalize_skill,
)
from app.domain.resume.exceptions import (
    ProfileNotFoundError,
    ResumeImportDraftNotFoundError,
    ResumeParseError,
    TailoredResumeDraftNotFoundError,
    UnsupportedResumeFormatError,
)
from app.domain.resume.lifecycle import ResumeLifecycleStatus
from app.domain.resume.prompts import TAILOR_RESUME_PROMPT
from app.domain.resume.repository import ResumeRepository
from app.domain.resume.schemas import (
    ParsedProfile,
    ProfileResponse,
    ResumeImportDetail,
    ResumeImportListItem,
    ResumeResponse,
    TailoredResume,
    TailoredResumeDraftCreate,
    TailoredResumeDraftListItem,
    TailoredResumeDraftResponse,
    TailoredWorkItem,
)
from app.domain.resume.truthfulness import sanitize_tailoring_output
from app.infrastructure.embeddings import embed_text
from app.infrastructure.llm_client import llm_chat_completion
from app.infrastructure.llm_utils import strip_code_fences
from app.infrastructure.resume_parser import extract_text_from_bytes, parse_resume_text

logger = get_logger("resume_service")

ProgressFn = Callable[..., Awaitable[None]]


def build_summary_text(profile: ParsedProfile) -> str:
    """Build the canonical text used to embed a profile for job matching.

    Concentrates the signal LLM matching cares about: headline, skills,
    preferred roles, and work-history highlights.
    """
    parts: list[str] = []
    if profile.headline:
        parts.append(profile.headline)
    if profile.skills:
        parts.append("Skills: " + ", ".join(profile.skills))
    if profile.preferred_roles:
        parts.append("Preferred roles: " + ", ".join(profile.preferred_roles))
    if profile.years_experience is not None:
        parts.append(f"Experience: {profile.years_experience} years")
    for item in profile.work_history:
        header = " ".join(filter(None, [item.title, "at", item.company]))
        if header.strip():
            parts.append(header)
        parts.extend(item.highlights)
    return "\n".join(p for p in parts if p).strip() or "No profile content"


def _untailored_fallback(
    profile: ParsedProfile, job_id: int, report: SkillMatchReport,
) -> TailoredResume:
    """Return the profile as-is when LLM tailoring is unavailable."""
    matched_set = set(report.matched_skills)
    other_skills = [s for s in profile.skills if normalize_skill(s) not in matched_set]
    return TailoredResume(
        job_id=job_id,
        professional_summary=profile.headline or None,
        full_name=profile.full_name,
        email=profile.email,
        phone=profile.phone,
        headline=profile.headline,
        education=list(profile.education),
        certifications=list(profile.certifications),
        work_history=[
            TailoredWorkItem(
                company=w.company,
                title=w.title,
                start=w.start,
                end=w.end,
                original_highlights=list(w.highlights),
                tailored_highlights=list(w.highlights),
            )
            for w in profile.work_history
        ],
        matched_skills=report.matched_skills,
        other_skills=other_skills,
        missing_keywords=report.missing_skills,
    )


class ResumeService:
    def __init__(self, repository: ResumeRepository) -> None:
        self._repository = repository

    async def upload_and_parse(
        self, *, user_id: int, filename: str, content: bytes,
        progress: ProgressFn | None = None,
    ) -> ResumeResponse:
        """Extract text, parse, and persist an active profile or import draft."""

        async def _emit(**kw: Any) -> None:
            if progress is not None:
                await progress(**kw)

        await _emit(status="running", message="Extracting text…")
        try:
            extracted = extract_text_from_bytes(content, filename, True)
            if isinstance(extracted, str):
                raw_text = extracted
                extraction_warnings = []
            else:
                raw_text = extracted.text
                extraction_warnings = extracted.warnings
        except ValueError as exc:
            raise UnsupportedResumeFormatError(str(exc)) from exc

        await _emit(message="Parsing with AI…")
        try:
            profile = await parse_resume_text(raw_text)
        except ValueError as exc:
            raise ResumeParseError(str(exc)) from exc

        await _emit(message="Embedding profile…")
        summary_text = build_summary_text(profile)
        await self._repository.lock_user_for_import(user_id)
        previous_profile = await self._repository.get_active_profile(user_id)
        is_first_import = previous_profile is None
        if is_first_import:
            matching_profile_changed = True
        else:
            assert previous_profile is not None
            matching_profile_changed = summary_text != previous_profile.summary_text
        if matching_profile_changed:
            embedding = await embed_text(summary_text)
        else:
            assert previous_profile is not None
            embedding = previous_profile.embedding

        lifecycle_status = (
            ResumeLifecycleStatus.ACTIVE
            if is_first_import
            else ResumeLifecycleStatus.DRAFT
        )
        resume = await self._repository.add_resume(
            user_id=user_id,
            filename=filename,
            raw_text=raw_text,
            is_active=is_first_import,
            lifecycle_status=lifecycle_status,
        )
        await self._repository.add_profile(
            user_id=user_id,
            resume_id=resume.id,
            parsed=profile.model_dump(),
            summary_text=summary_text,
            embedding=embedding,
        )
        logger.info("resume_parsed", user_id=user_id, resume_id=resume.id)
        return ResumeResponse(
            resume_id=resume.id,
            filename=resume.filename,
            is_active=resume.is_active,
            lifecycle_status=ResumeLifecycleStatus(resume.lifecycle_status),
            profile=profile,
            matching_profile_changed=is_first_import,
            extraction_warnings=extraction_warnings,
        )

    async def get_profile(self, user_id: int) -> ProfileResponse:
        profile_row = await self._repository.get_active_profile(user_id)
        if profile_row is None:
            raise ProfileNotFoundError(user_id)
        return ProfileResponse(
            profile_id=profile_row.id,
            resume_id=profile_row.resume_id,
            profile=ParsedProfile.model_validate(profile_row.parsed),
        )

    async def update_profile(
        self, *, user_id: int, profile: ParsedProfile
    ) -> ProfileResponse:
        """Persist user edits and re-embed the corrected profile."""
        profile_row = await self._repository.get_active_profile(user_id)
        if profile_row is None:
            raise ProfileNotFoundError(user_id)

        summary_text = build_summary_text(profile)
        matching_profile_changed = summary_text != profile_row.summary_text
        if matching_profile_changed:
            embedding = await embed_text(summary_text)
        else:
            embedding = profile_row.embedding
        await self._repository.update_profile_parsed(
            profile=profile_row,
            parsed=profile.model_dump(),
            summary_text=summary_text,
            embedding=embedding,
        )
        logger.info(
            "profile_updated",
            user_id=user_id,
            profile_id=profile_row.id,
            matching_profile_changed=matching_profile_changed,
        )
        return ProfileResponse(
            profile_id=profile_row.id,
            resume_id=profile_row.resume_id,
            profile=profile,
            matching_profile_changed=matching_profile_changed,
        )

    async def list_import_drafts(self, user_id: int) -> list[ResumeImportListItem]:
        drafts = await self._repository.list_import_drafts(user_id)
        return [
            ResumeImportListItem(
                resume_id=resume.id,
                filename=resume.filename,
                created_at=resume.created_at,
                lifecycle_status=ResumeLifecycleStatus(resume.lifecycle_status),
            )
            for resume, _profile in drafts
        ]

    async def get_import_draft(
        self, *, user_id: int, resume_id: int
    ) -> ResumeImportDetail:
        draft = await self._repository.get_import_draft(
            user_id=user_id, resume_id=resume_id
        )
        if draft is None:
            raise ResumeImportDraftNotFoundError(resume_id)
        resume, profile = draft
        return ResumeImportDetail(
            resume_id=resume.id,
            filename=resume.filename,
            created_at=resume.created_at,
            is_active=resume.is_active,
            lifecycle_status=ResumeLifecycleStatus(resume.lifecycle_status),
            profile=ParsedProfile.model_validate(profile.parsed),
        )

    async def approve_import_draft(
        self, *, user_id: int, resume_id: int
    ) -> ProfileResponse:
        profile = await self._repository.approve_import_draft(
            user_id=user_id, resume_id=resume_id
        )
        if profile is None:
            raise ResumeImportDraftNotFoundError(resume_id)
        return ProfileResponse(
            profile_id=profile.id,
            resume_id=profile.resume_id,
            profile=ParsedProfile.model_validate(profile.parsed),
            matching_profile_changed=True,
        )

    async def reject_import_draft(self, *, user_id: int, resume_id: int) -> None:
        rejected = await self._repository.reject_import_draft(
            user_id=user_id, resume_id=resume_id
        )
        if not rejected:
            raise ResumeImportDraftNotFoundError(resume_id)

    # -- saved tailored drafts ---------------------------------------------

    @staticmethod
    def _draft_response(draft: Any) -> TailoredResumeDraftResponse:
        return TailoredResumeDraftResponse(
            id=draft.id,
            job_id=draft.job_id,
            source_profile_id=draft.source_profile_id,
            job_title=draft.job_title,
            job_company=draft.job_company,
            created_at=draft.created_at,
            updated_at=draft.updated_at,
            content=TailoredResume.model_validate(draft.content),
        )

    @staticmethod
    def _draft_list_item(draft: Any) -> TailoredResumeDraftListItem:
        return TailoredResumeDraftListItem(
            id=draft.id,
            job_id=draft.job_id,
            source_profile_id=draft.source_profile_id,
            job_title=draft.job_title,
            job_company=draft.job_company,
            created_at=draft.created_at,
            updated_at=draft.updated_at,
        )

    async def create_tailored_draft(
        self,
        *,
        user_id: int,
        payload: TailoredResumeDraftCreate,
        job_repository: JobRepository,
    ) -> TailoredResumeDraftResponse:
        profile = await self._repository.get_active_profile(user_id)
        if profile is None:
            raise ProfileNotFoundError(user_id)

        job = await job_repository.get_visible_by_id(
            user_id=user_id, job_id=payload.job_id
        )
        if job is None:
            raise JobNotFoundError(payload.job_id)

        draft = await self._repository.create_tailored_draft(
            user_id=user_id,
            job_id=payload.job_id,
            source_profile_id=profile.id,
            job_title=job.title,
            job_company=job.company,
            content=payload.content.model_dump(mode="json"),
        )
        return self._draft_response(draft)

    async def list_tailored_drafts(
        self, *, user_id: int
    ) -> list[TailoredResumeDraftListItem]:
        drafts = await self._repository.list_tailored_drafts(user_id=user_id)
        return [self._draft_list_item(draft) for draft in drafts]

    async def get_tailored_draft(
        self, *, user_id: int, draft_id: int
    ) -> TailoredResumeDraftResponse:
        draft = await self._repository.get_tailored_draft(
            user_id=user_id, draft_id=draft_id
        )
        if draft is None:
            raise TailoredResumeDraftNotFoundError(draft_id)
        return self._draft_response(draft)

    async def delete_tailored_draft(
        self, *, user_id: int, draft_id: int
    ) -> None:
        deleted = await self._repository.delete_tailored_draft(
            user_id=user_id, draft_id=draft_id
        )
        if not deleted:
            raise TailoredResumeDraftNotFoundError(draft_id)

    # -- resume tailoring --------------------------------------------------

    async def tailor_for_job(
        self,
        *,
        user_id: int,
        job_id: int,
        job_repository: JobRepository,
    ) -> TailoredResume:
        """Tailor the user's work history highlights for a specific job.

        Uses deterministic skill matching (free) to ground the LLM, then
        calls the LLM to rewrite highlights for relevance. Never invents
        skills or experience — only rephrases what already exists.
        """
        profile_row = await self._repository.get_active_profile(user_id)
        if profile_row is None:
            raise ProfileNotFoundError(user_id)

        profile = ParsedProfile.model_validate(profile_row.parsed)
        job = await job_repository.get_visible_by_id(user_id=user_id, job_id=job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if not job.description:
            # Return untailored — nothing to match against.
            return _untailored_fallback(profile, job_id, SkillMatchReport())

        # Deterministic: which profile skills are in the JD?
        jd_skills = extract_jd_skills(job.description)
        report = compute_skill_match(jd_skills, profile.model_dump())

        # Build work history JSON for the prompt (original highlights only).
        work_json = [
            {
                "company": w.company,
                "title": w.title,
                "start": w.start,
                "end": w.end,
                "highlights": w.highlights,
            }
            for w in profile.work_history
        ]

        prompt = TAILOR_RESUME_PROMPT.format(
            job_description=job.description,
            work_history_json=json.dumps(work_json, default=str),
            matched_skills=json.dumps(report.matched_skills),
            candidate_skills=json.dumps(profile.skills),
            missing_skills=json.dumps(report.missing_skills),
        )

        try:
            response, model = await llm_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                json_mode=True,
            )
            raw = strip_code_fences(response.choices[0].message.content or "{}")
            data = json.loads(raw)
        except Exception:
            logger.warning(
                "resume.tailor.llm_failed",
                user_id=user_id,
                job_id=job_id,
                exc_info=True,
            )
            # Graceful fallback: return untailored profile with skill report.
            return _untailored_fallback(profile, job_id, report)

        try:
            sanitized = sanitize_tailoring_output(
                profile,
                data,
                report.missing_skills,
            )
        except Exception:
            logger.warning(
                "resume.tailor.output_invalid",
                user_id=user_id,
                job_id=job_id,
                exc_info=True,
            )
            return _untailored_fallback(profile, job_id, report)

        matched = list(report.matched_skills)
        matched_set = set(matched)
        other = [s for s in profile.skills if normalize_skill(s) not in matched_set]
        missing = list(report.missing_skills)
        tailored_wh = [
            TailoredWorkItem(
                company=source.company,
                title=source.title,
                start=source.start,
                end=source.end,
                original_highlights=list(source.highlights),
                tailored_highlights=sanitized_bullets,
            )
            for source, sanitized_bullets in zip(
                profile.work_history,
                sanitized.tailored_highlights,
                strict=True,
            )
        ]

        logger.info(
            "resume.tailored",
            user_id=user_id,
            job_id=job_id,
            model=model,
            roles=len(tailored_wh),
            summary_fallback=sanitized.summary_fallback,
            role_fallbacks=sanitized.role_fallbacks,
            bullet_fallbacks=sanitized.bullet_fallbacks,
        )
        return TailoredResume(
            job_id=job_id,
            professional_summary=sanitized.professional_summary,
            full_name=profile.full_name,
            email=profile.email,
            phone=profile.phone,
            headline=profile.headline,
            education=list(profile.education),
            certifications=list(profile.certifications),
            work_history=tailored_wh,
            matched_skills=matched,
            other_skills=other,
            missing_keywords=missing,
        )
