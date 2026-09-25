"""Grounded interview preparation from immutable application history."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interview.application_prompts import APPLICATION_INTERVIEW_PROMPT, PROMPT_VERSION
from app.domain.interview.application_schemas import (
    ApplicationInterviewEvidence,
    ApplicationInterviewQuestion,
    ApplicationInterviewRequest,
    ApplicationInterviewResponse,
)
from app.domain.interview.exceptions import InterviewGenerationError
from app.domain.resume.repository import ResumeRepository
from app.infrastructure.llm_client import llm_chat_completion
from app.infrastructure.llm_utils import strip_code_fences
from app.models.application import Application
from app.models.application_interview_prep import ApplicationInterviewPrep
from app.models.application_package import ApplicationPackage
from app.models.application_submission import ApplicationSubmission
from app.models.job import Job


class ApplicationInterviewNotFoundError(Exception):
    pass


class ApplicationInterviewContextInvalidError(Exception):
    pass


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _passages(source: str, value: object) -> dict[str, str]:
    """Make bounded, addressable passages from semantic text, never artifact bytes."""
    result: dict[str, str] = {}

    def visit(path: str, item: object) -> None:
        if len(result) >= 120:
            return
        if isinstance(item, str) and item.strip():
            result[f"{source}:{path}"] = item[:1000]
        elif isinstance(item, dict):
            for key in sorted(item):
                if key in {
                    "provenance", "source_url", "model", "schema_version",
                    "source_schema_version", "source_revision_sha256",
                    "mode", "tone", "is_edited",
                }:
                    continue
                visit(f"{path}.{key}" if path else str(key), item[key])
        elif isinstance(item, list):
            for index, child in enumerate(item):
                visit(f"{path}[{index}]", child)

    visit("", value)
    return result


class ApplicationInterviewService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_generate(
        self, *, user_id: int, application_id: int, request: ApplicationInterviewRequest,
    ) -> ApplicationInterviewResponse:
        application = await self._session.scalar(select(Application).where(
            Application.id == application_id, Application.user_id == user_id,
        ))
        if application is None:
            raise ApplicationInterviewNotFoundError

        submission = None
        if request.submission_id is not None:
            submission = await self._session.scalar(select(ApplicationSubmission).where(
                ApplicationSubmission.id == request.submission_id,
                ApplicationSubmission.application_id == application_id,
                ApplicationSubmission.user_id == user_id,
            ))
            if submission is None:
                raise ApplicationInterviewNotFoundError

        selected = submission is not None and submission.material_choice == "used_selected_package"
        if selected and request.current_profile_fallback:
            raise ApplicationInterviewContextInvalidError(
                "Choose either submitted material or fallback"
            )
        if not selected and not request.current_profile_fallback:
            raise ApplicationInterviewContextInvalidError(
                "Explicit current-profile fallback is required"
            )

        source_submission_id: int | None
        if selected:
            assert submission is not None and submission.package_id is not None
            package = await self._session.scalar(select(ApplicationPackage).where(
                ApplicationPackage.id == submission.package_id,
                ApplicationPackage.application_id == application_id,
            ))
            if package is None:
                raise ApplicationInterviewNotFoundError
            source_label = "confirmed_submission"
            resume_snapshot = package.content.get("resume")
            resume_content = resume_snapshot.get("content") if isinstance(
                resume_snapshot, dict
            ) else None
            semantic = {"job": package.content.get("job"),
                        "resume": resume_content,
                        "cover_letter": package.content.get("cover_letter"),
                        "manual_questions": package.content.get("manual_questions")}
            identity = {"submission_id": submission.id, "package_id": package.id,
                        "package_hash": package.content_sha256, "semantic": semantic}
            source_submission_id = submission.id
        else:
            profile = await ResumeRepository(self._session).get_active_profile(user_id)
            if profile is None:
                raise ApplicationInterviewContextInvalidError(
                    "An active profile is required for fallback"
                )
            job = await self._session.scalar(select(Job).where(Job.id == application.job_id))
            if job is None:
                raise ApplicationInterviewNotFoundError
            source_label = "current_profile_fallback"
            semantic = {"job": {"title": job.title, "company": job.company,
                                "location": job.location, "description": job.description},
                        "profile": profile.parsed}
            identity = {"submission_id": submission.id if submission else None,
                        "profile_id": profile.id, "resume_id": profile.resume_id,
                        "semantic": semantic}
            source_submission_id = submission.id if submission else None

        context_hash = _hash({"source_label": source_label, "identity": identity,
                              "prompt_version": PROMPT_VERSION})
        cache_key = _hash({"context_hash": context_hash, "source_label": source_label,
                           "stage": request.stage, "focus_areas": request.focus_areas,
                           "question_count": request.question_count,
                           "prompt_version": PROMPT_VERSION})
        cached_row = await self._session.scalar(select(ApplicationInterviewPrep).where(
            ApplicationInterviewPrep.user_id == user_id,
            ApplicationInterviewPrep.application_id == application_id,
            ApplicationInterviewPrep.cache_key == cache_key,
        ))
        if cached_row is not None and not request.force_refresh:
            return self._response(cached_row, cached=True)

        passages: dict[str, str] = {}
        for name, value in semantic.items():
            passages.update(_passages(name, value))
        candidate_prefixes = ("resume:", "cover_letter:") if selected else ("profile:",)
        if selected and not any(locator.startswith("resume:") for locator in passages):
            raise ApplicationInterviewContextInvalidError(
                "Selected package has no usable resume context"
            )
        candidate_locators = {
            locator for locator in passages if locator.startswith(candidate_prefixes)
        }
        if not candidate_locators:
            raise ApplicationInterviewContextInvalidError(
                "No usable candidate interview context is available"
            )
        context = json.dumps({"source_label": source_label, "passages": passages,
                              "focus_areas": request.focus_areas}, ensure_ascii=False,
                             sort_keys=True)
        try:
            response, model = await llm_chat_completion(
                messages=[
                    {"role": "system", "content": APPLICATION_INTERVIEW_PROMPT.format(
                        question_count=request.question_count, stage=request.stage)},
                    {"role": "user", "content": context},
                ], temperature=0.5, json_mode=True,
            )
        except Exception as exc:
            raise InterviewGenerationError("Interview provider unavailable") from exc
        try:
            payload = json.loads(strip_code_fences(response.choices[0].message.content or ""))
            raw_questions = payload["questions"]
            if not isinstance(raw_questions, list) or len(raw_questions) != request.question_count:
                raise ValueError("Wrong question count")
            questions = [
                ApplicationInterviewQuestion.model_validate(item) for item in raw_questions
            ]
            if any(locator not in passages for question in questions
                   for locator in question.source_locators):
                raise ValueError("Unknown source locator")
            if any(not candidate_locators.intersection(question.source_locators)
                   for question in questions):
                raise ValueError("Suggested answer lacks candidate evidence")
        except (
            ValueError, KeyError, IndexError, TypeError, AttributeError, ValidationError
        ) as exc:
            raise InterviewGenerationError("Invalid interview provider output") from exc

        values: dict[str, Any] = {
            "user_id": user_id, "application_id": application_id,
            "submission_id": source_submission_id, "context_hash": context_hash,
            "cache_key": cache_key, "stage": request.stage,
            "focus_areas": request.focus_areas, "question_count": request.question_count,
            "prompt_version": PROMPT_VERSION,
            "questions": [question.model_dump() for question in questions],
            "evidence": [{"locator": locator, "source": locator.split(":", 1)[0]}
                         for locator in sorted({locator for question in questions
                                                for locator in question.source_locators})],
            "source_label": source_label, "model": model,
        }
        insert_stmt = pg_insert(ApplicationInterviewPrep).values(**values)
        row = (await self._session.execute(insert_stmt.on_conflict_do_update(
            index_elements=[ApplicationInterviewPrep.user_id,
                            ApplicationInterviewPrep.application_id,
                            ApplicationInterviewPrep.cache_key],
            set_={**{key: getattr(insert_stmt.excluded, key) for key in values
                     if key not in {"user_id", "application_id", "cache_key"}},
                  "updated_at": datetime.now(UTC)},
        ).returning(ApplicationInterviewPrep))).scalar_one()
        await self._session.commit()
        return self._response(row, cached=False)

    @staticmethod
    def _response(row: ApplicationInterviewPrep, *, cached: bool) -> ApplicationInterviewResponse:
        return ApplicationInterviewResponse(
            application_id=row.application_id, submission_id=row.submission_id,
            source_label=row.source_label, stage=row.stage,
            focus_areas=row.focus_areas, question_count=row.question_count,
            context_hash=row.context_hash, cached=cached,
            questions=[ApplicationInterviewQuestion.model_validate(item) for item in row.questions],
            evidence=[ApplicationInterviewEvidence.model_validate(item) for item in row.evidence],
            model=row.model, created_at=row.created_at, updated_at=row.updated_at,
        )
