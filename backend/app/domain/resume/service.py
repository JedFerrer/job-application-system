"""Resume domain service: orchestrates parse -> embed -> persist.

Pure-ish business logic; no FastAPI/HTTP concerns. Raises domain exceptions.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger
from app.domain.jobs.repository import JobRepository
from app.domain.matching.skill_matcher import SkillMatchReport, compute_skill_match, extract_jd_skills
from app.domain.resume.exceptions import (
    ProfileNotFoundError,
    ResumeParseError,
    UnsupportedResumeFormatError,
)
from app.domain.resume.prompts import TAILOR_RESUME_PROMPT
from app.domain.resume.repository import ResumeRepository
from app.domain.resume.schemas import (
    ParsedProfile,
    ProfileResponse,
    ResumeResponse,
    TailoredResume,
    TailoredWorkItem,
)
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
    other_skills = [s for s in profile.skills if s not in matched_set]
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
        """Extract text, LLM-parse, embed, and persist a new active profile."""

        async def _emit(**kw: Any) -> None:
            if progress is not None:
                await progress(**kw)

        await _emit(status="running", message="Extracting text…")
        try:
            raw_text = extract_text_from_bytes(content, filename)
        except ValueError as exc:
            raise UnsupportedResumeFormatError(str(exc)) from exc

        await _emit(message="Parsing with AI…")
        try:
            profile = await parse_resume_text(raw_text)
        except ValueError as exc:
            raise ResumeParseError(str(exc)) from exc

        await _emit(message="Embedding profile…")
        summary_text = build_summary_text(profile)
        embedding = await embed_text(summary_text)

        await self._repository.deactivate_existing(user_id)
        resume = await self._repository.add_resume(
            user_id=user_id, filename=filename, raw_text=raw_text
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
            profile=profile,
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
        embedding = await embed_text(summary_text)
        await self._repository.update_profile_parsed(
            profile=profile_row,
            parsed=profile.model_dump(),
            summary_text=summary_text,
            embedding=embedding,
        )
        logger.info("profile_updated", user_id=user_id, profile_id=profile_row.id)
        return ProfileResponse(
            profile_id=profile_row.id,
            resume_id=profile_row.resume_id,
            profile=profile,
        )

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
        job = await job_repository.get_by_id(job_id)
        if job is None or not job.description:
            # Return untailored — nothing to match against.
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
                matched_skills=[],
                other_skills=list(profile.skills),
                missing_keywords=[],
            )

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

        # Parse tailored work history from LLM response.
        professional_summary = data.get("professional_summary")
        if not isinstance(professional_summary, str):
            professional_summary = profile.headline or None

        tailored_wh: list[TailoredWorkItem] = []
        llm_items = data.get("tailored_work_history") or []
        for i, llm_item in enumerate(llm_items if isinstance(llm_items, list) else []):
            if not isinstance(llm_item, dict):
                continue
            original = profile.work_history[i] if i < len(profile.work_history) else None
            tailored_wh.append(
                TailoredWorkItem(
                    company=original.company if original else llm_item.get("company"),
                    title=original.title if original else llm_item.get("title"),
                    start=original.start if original else None,
                    end=original.end if original else None,
                    original_highlights=list(original.highlights) if original else [],
                    tailored_highlights=(
                        llm_item.get("tailored_highlights")
                        if isinstance(llm_item.get("tailored_highlights"), list)
                        else []
                    ),
                )
            )

        matched = data.get("matched_skills_ordered") or []
        if not isinstance(matched, list):
            matched = []
        other = data.get("other_skills") or []
        if not isinstance(other, list):
            other = []
        missing = data.get("missing_keywords") or []
        if not isinstance(missing, list):
            missing = []

        logger.info(
            "resume.tailored",
            user_id=user_id,
            job_id=job_id,
            model=model,
            roles=len(tailored_wh),
        )
        return TailoredResume(
            job_id=job_id,
            professional_summary=professional_summary,
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
