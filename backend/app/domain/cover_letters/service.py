"""Cover-letter service: generate (grounded) -> persist -> edit (Phase 4)."""

from __future__ import annotations

import json

from app.core.logging import get_logger
from app.domain.cover_letters.exceptions import (
    CoverLetterGenerationError,
    CoverLetterNotFoundError,
)
from app.domain.cover_letters.prompts import COVER_LETTER_SYSTEM_PROMPT
from app.domain.cover_letters.repository import CoverLetterRepository
from app.domain.cover_letters.schemas import CoverLetterResponse
from app.domain.jobs.exceptions import JobNotFoundError
from app.domain.jobs.repository import JobRepository
from app.domain.resume.exceptions import ProfileNotFoundError
from app.domain.resume.repository import ResumeRepository
from app.infrastructure.llm_client import LLMUnavailableError, llm_chat_completion
from app.infrastructure.llm_utils import strip_code_fences

logger = get_logger("cover_letter_service")

_DEFAULT_TONE = "professional and warm"


class CoverLetterService:
    def __init__(
        self,
        *,
        repository: CoverLetterRepository,
        resume_repository: ResumeRepository,
        job_repository: JobRepository,
    ) -> None:
        self._repo = repository
        self._resumes = resume_repository
        self._jobs = job_repository

    async def generate(self, *, user_id: int, job_id: int, tone: str | None) -> CoverLetterResponse:
        profile = await self._resumes.get_active_profile(user_id)
        if profile is None:
            raise ProfileNotFoundError(user_id)
        job = await self._jobs.get_visible_by_id(user_id=user_id, job_id=job_id)
        if job is None:
            raise JobNotFoundError(job_id)

        candidate_profile = dict(profile.parsed)
        candidate_profile.pop("links", None)
        candidate_profile.pop("projects", None)

        content, model = await self._generate_with_llm(
            candidate_profile=candidate_profile,
            job_title=job.title,
            company=job.company,
            job_description=job.description or "",
            tone=tone or _DEFAULT_TONE,
        )
        cover_letter = await self._repo.create(
            user_id=user_id,
            job_id=job_id,
            content=content,
            tone=tone,
            model=model,
        )
        # Auto-link to the application for this job if one exists.
        await self._link_cover_letter_to_application(
            user_id=user_id, job_id=job_id, cover_letter_id=cover_letter.id
        )
        logger.info(
            "cover_letter.generated",
            user_id=user_id,
            job_id=job_id,
            cover_letter_id=cover_letter.id,
        )
        return CoverLetterResponse.model_validate(cover_letter)

    async def get_for_job(self, *, user_id: int, job_id: int) -> CoverLetterResponse | None:
        """Return the most recent cover letter for a job, or None if absent."""
        cover_letter = await self._repo.get_for_job(user_id=user_id, job_id=job_id)
        if cover_letter is None:
            return None
        return CoverLetterResponse.model_validate(cover_letter)

    async def get(self, *, user_id: int, cover_letter_id: int) -> CoverLetterResponse:
        cover_letter = await self._repo.get(user_id=user_id, cover_letter_id=cover_letter_id)
        if cover_letter is None:
            raise CoverLetterNotFoundError(cover_letter_id)
        return CoverLetterResponse.model_validate(cover_letter)

    async def update(
        self, *, user_id: int, cover_letter_id: int, content: str
    ) -> CoverLetterResponse:
        cover_letter = await self._repo.get(user_id=user_id, cover_letter_id=cover_letter_id)
        if cover_letter is None:
            raise CoverLetterNotFoundError(cover_letter_id)
        updated = await self._repo.update_content(cover_letter=cover_letter, content=content)
        return CoverLetterResponse.model_validate(updated)

    async def _link_cover_letter_to_application(
        self, *, user_id: int, job_id: int, cover_letter_id: int
    ) -> None:
        """If an application exists for this (user, job), set its cover_letter_id."""
        await self._repo.link_to_application(
            user_id=user_id, job_id=job_id, cover_letter_id=cover_letter_id
        )

    async def _generate_with_llm(
        self,
        *,
        candidate_profile: dict[str, object],
        job_title: str,
        company: str | None,
        job_description: str,
        tone: str,
    ) -> tuple[str, str]:
        user_payload = {
            "candidate_profile": candidate_profile,
            "job_title": job_title,
            "company": company,
            "job_description": job_description,
            "tone": tone,
        }
        messages = [
            {"role": "system", "content": COVER_LETTER_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, default=str)},
        ]
        try:
            response, model = await llm_chat_completion(
                messages=messages, json_mode=True, temperature=0.6
            )
        except LLMUnavailableError as exc:
            raise CoverLetterGenerationError(str(exc)) from exc

        raw = response.choices[0].message.content or "{}"
        try:
            data = json.loads(strip_code_fences(raw))
        except json.JSONDecodeError as exc:
            raise CoverLetterGenerationError("LLM returned invalid JSON") from exc

        content = data.get("content") if isinstance(data, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise CoverLetterGenerationError("LLM returned no cover letter content")
        return content.strip(), model
