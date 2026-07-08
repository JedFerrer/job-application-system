"""Interview prep service: cache-aware LLM question generation (Phase 2)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.core.logging import get_logger
from app.domain.interview.exceptions import InterviewGenerationError
from app.domain.interview.prompts import INTERVIEW_PREP_PROMPT
from app.domain.interview.repository import InterviewRepository
from app.domain.interview.schemas import (
    InterviewPrepRequest,
    InterviewPrepResponse,
    InterviewQuestion,
)
from app.domain.jobs.exceptions import JobNotFoundError
from app.domain.matching.exceptions import NoActiveProfileError
from app.infrastructure.llm_client import LLMUnavailableError, llm_chat_completion
from app.infrastructure.llm_utils import strip_code_fences
from app.models.interview_prep import InterviewPrep
from app.models.job import Job
from app.models.resume import CandidateProfile

logger = get_logger(__name__)


class InterviewService:
    def __init__(self, repository: InterviewRepository) -> None:
        self._repo = repository

    async def get_or_generate(
        self,
        *,
        user_id: int,
        job_id: int,
        request: InterviewPrepRequest,
        force_refresh: bool = False,
    ) -> InterviewPrepResponse:
        if not force_refresh:
            cached = await self._repo.get_prep(user_id=user_id, job_id=job_id)
            if cached is not None:
                return self._to_response(cached, cached=True)

        job = await self._repo.get_visible_job(user_id=user_id, job_id=job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        profile = await self._repo.get_active_profile(user_id)
        if profile is None:
            raise NoActiveProfileError

        questions, model = await self._generate(job, profile, request)
        prep = await self._repo.upsert_prep(
            user_id=user_id,
            job_id=job_id,
            questions=[q.model_dump() for q in questions],
            focus_areas=request.focus_areas,
            model=model,
        )
        logger.info(
            "interview.generated",
            user_id=user_id,
            job_id=job_id,
            count=len(questions),
        )
        return self._to_response(prep, cached=False)

    async def _generate(
        self,
        job: Job,
        profile: CandidateProfile,
        request: InterviewPrepRequest,
    ) -> tuple[list[InterviewQuestion], str | None]:
        system_prompt = INTERVIEW_PREP_PROMPT.replace(
            "{question_count}", str(request.question_count)
        )
        user_content = self._build_context(job, profile, request)

        try:
            response, model = await llm_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.5,
                json_mode=True,
            )
        except LLMUnavailableError as exc:
            raise InterviewGenerationError(str(exc)) from exc

        content = response.choices[0].message.content or ""
        questions = self._parse_questions(content, request.question_count)
        return questions, model

    @staticmethod
    def _build_context(
        job: Job, profile: CandidateProfile, request: InterviewPrepRequest
    ) -> str:
        parsed: dict[str, Any] = profile.parsed or {}
        skills = parsed.get("skills") or []
        seniority = parsed.get("seniority") or "unspecified"
        headline = parsed.get("headline") or ""
        focus = ", ".join(request.focus_areas) if request.focus_areas else "none"
        return (
            f"JOB TITLE: {job.title}\n"
            f"COMPANY: {job.company or 'unspecified'}\n"
            f"LOCATION: {job.location or 'unspecified'}\n"
            f"JOB DESCRIPTION:\n{(job.description or '')[:6000]}\n\n"
            f"CANDIDATE HEADLINE: {headline}\n"
            f"CANDIDATE SENIORITY: {seniority}\n"
            f"CANDIDATE SKILLS: {', '.join(map(str, skills)) or 'unspecified'}\n\n"
            f"FOCUS AREAS: {focus}\n"
        )

    @staticmethod
    def _parse_questions(
        content: str, expected_count: int
    ) -> list[InterviewQuestion]:
        try:
            payload = json.loads(strip_code_fences(content))
        except json.JSONDecodeError as exc:
            raise InterviewGenerationError("LLM returned non-JSON output") from exc

        raw_questions = payload.get("questions")
        if not isinstance(raw_questions, list) or not raw_questions:
            raise InterviewGenerationError("LLM returned no questions")

        questions: list[InterviewQuestion] = []
        for item in raw_questions:
            try:
                questions.append(InterviewQuestion.model_validate(item))
            except ValidationError:
                # Skip malformed entries rather than failing the whole request.
                logger.warning("interview.question_invalid", item=item)
                continue

        if not questions:
            raise InterviewGenerationError("No valid questions in LLM output")
        return questions[:expected_count]

    @staticmethod
    def _to_response(prep: InterviewPrep, *, cached: bool) -> InterviewPrepResponse:
        questions = [
            InterviewQuestion.model_validate(q) for q in (prep.questions or [])
        ]
        return InterviewPrepResponse(
            job_id=prep.job_id,
            questions=questions,
            focus_areas=list(prep.focus_areas or []),
            model=prep.model,
            cached=cached,
            created_at=prep.created_at,
        )
