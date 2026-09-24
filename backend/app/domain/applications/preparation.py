"""Owned preparation state and immutable package orchestration."""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.domain.applications.artifact_lifecycle import artifact_summary
from app.domain.applications.exceptions import (
    ApplicationJobNotActionableError,
    ApplicationNotFoundError,
    PackageApprovalConflictError,
    PackageIdempotencyConflictError,
    PreparationSelectionInvalidError,
    PreparationVersionConflictError,
)
from app.domain.applications.schemas import (
    ApplicationPackageResponse,
    ApprovePreparationRequest,
    CreatePackageRequest,
    ManualQuestion,
    PreparationPatch,
    PreparationResponse,
)
from app.domain.cover_letters.repository import CoverLetterRepository
from app.domain.jobs.repository import JobRepository
from app.domain.resume.exceptions import TailoredResumeDraftNotFoundError
from app.domain.resume.repository import ResumeRepository
from app.domain.resume.schemas import TailoredResume
from app.domain.resume.service import ResumeService
from app.models.application import Application
from app.models.application_artifact import ApplicationArtifact
from app.models.application_event import ApplicationEvent
from app.models.application_package import ApplicationPackage
from app.models.application_preparation import ApplicationPreparation
from app.models.cover_letter import CoverLetter
from app.models.resume import TailoredResumeDraft
from app.models.user_job_match import UserJobMatch

PACKAGE_SCHEMA_VERSION = "application-package-v1"
_FIT_FIELDS = ("title", "company", "location", "job_type", "source", "source_url", "description")
_MUTABLE_PATCH_FIELDS = frozenset(
    {
        "fit_acknowledged",
        "resume_draft_id",
        "cover_letter_mode",
        "cover_letter_id",
        "manual_questions",
    }
)


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_value(value: object) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _normalized_fit_skill_list(values: list[str] | None) -> list[str]:
    """Canonicalize a fit-context skill set without changing its values.

    Match skill collections communicate membership, not priority.  Preserve
    duplicate and string semantics while making their stored ordering unable to
    invalidate a fit acknowledgement.
    """
    return sorted(values or [])


def _fit_match_projection(match: UserJobMatch) -> dict[str, object]:
    """Return the order-stable subset of match data relevant to fit review."""
    deterministic_match = dict(match.deterministic_match or {})
    for key in ("matched_skills", "missing_skills"):
        value = deterministic_match.get(key)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            deterministic_match[key] = _normalized_fit_skill_list(value)
    return {
        "vector_score": match.vector_score,
        "llm_score": match.llm_score,
        "confidence": match.confidence,
        "matched_skills": _normalized_fit_skill_list(match.matched_skills),
        "missing_keywords": _normalized_fit_skill_list(match.missing_keywords),
        "deterministic_match": deterministic_match,
    }


class ApplicationPreparationService:
    """Preparation reads are pure; writes serialize on the owning application."""

    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session
        self._jobs = JobRepository(session)
        self._resumes = ResumeRepository(session)
        self._resume_service = ResumeService(self._resumes)
        self._letters = CoverLetterRepository(session)

    async def get(self, *, user_id: int, application_id: int) -> PreparationResponse:
        application = await self._application(user_id=user_id, application_id=application_id)
        preparation = await self._session.get(ApplicationPreparation, application.id)
        return await self._project(
            application=application, preparation=preparation, user_id=user_id
        )

    async def patch(
        self, *, user_id: int, application_id: int, payload: PreparationPatch
    ) -> PreparationResponse:
        application = await self._locked_application(user_id=user_id, application_id=application_id)
        await self._require_actionable(application)
        preparation = await self._session.get(
            ApplicationPreparation, application.id, with_for_update=True
        )
        mutable_fields = payload.model_fields_set & _MUTABLE_PATCH_FIELDS
        created = False
        if preparation is None:
            if payload.expected_version != 0:
                raise PreparationVersionConflictError()
            if not mutable_fields:
                return await self._project(
                    application=application, preparation=None, user_id=user_id
                )
            preparation = ApplicationPreparation(application_id=application.id)
            self._session.add(preparation)
            await self._session.flush()
            created = True
        elif preparation.version != payload.expected_version:
            raise PreparationVersionConflictError()
        elif not mutable_fields:
            return await self._project(
                application=application, preparation=preparation, user_id=user_id
            )

        changed = await self._apply_patch(
            application=application, preparation=preparation, user_id=user_id, payload=payload
        )
        if changed or created:
            preparation.version += 1
            preparation.approved_package_id = None
            preparation.reviewable_package_id = None
            if changed:
                await self._event(
                    application,
                    "preparation_updated",
                    {"changed_fields": sorted(changed)},
                )
        await self._session.flush()
        return await self._project(
            application=application, preparation=preparation, user_id=user_id
        )

    async def create_package(
        self,
        *,
        user_id: int,
        application_id: int,
        payload: CreatePackageRequest,
        idempotency_key: str,
    ) -> tuple[ApplicationPackageResponse, bool]:
        application = await self._locked_application(user_id=user_id, application_id=application_id)
        await self._require_actionable(application)
        preparation = await self._session.get(
            ApplicationPreparation, application.id, with_for_update=True
        )
        if preparation is None or preparation.version != payload.expected_version:
            raise PreparationVersionConflictError()
        snapshot = await self._snapshot(
            application=application, preparation=preparation, user_id=user_id, strict=True
        )
        content_hash = _canonical_hash(snapshot)
        by_key = await self._package_by_key(application.id, idempotency_key)
        if by_key is not None:
            if by_key.content_sha256 != content_hash:
                raise PackageIdempotencyConflictError()
            preparation.reviewable_package_id = by_key.id
            await self._session.flush()
            return await self._package_response(by_key), False
        by_content = await self._package_by_hash(application.id, content_hash)
        if by_content is not None:
            preparation.reviewable_package_id = by_content.id
            await self._session.flush()
            return await self._package_response(by_content), False
        package = ApplicationPackage(
            application_id=application.id,
            revision=preparation.next_package_revision,
            content=snapshot,
            content_sha256=content_hash,
            schema_version=PACKAGE_SCHEMA_VERSION,
            idempotency_key=idempotency_key,
            resume_draft_id=preparation.resume_draft_id,
            cover_letter_id=preparation.cover_letter_id,
        )
        self._session.add(package)
        # ``ApplicationPackage.id`` is generated by the database.  Flush it
        # before recording the preparation's durable current-review pointer.
        await self._session.flush()
        if package.id is None:
            raise RuntimeError("New application package was not assigned an ID")
        preparation.next_package_revision += 1
        preparation.reviewable_package_id = package.id
        await self._session.flush()
        await self._event(
            application, "package_created", {"package_id": package.id, "revision": package.revision}
        )
        return await self._package_response(package), True

    async def get_package(
        self, *, user_id: int, application_id: int, package_id: int
    ) -> ApplicationPackageResponse:
        await self._application(user_id=user_id, application_id=application_id)
        package = await self._session.scalar(
            select(ApplicationPackage).where(
                ApplicationPackage.id == package_id,
                ApplicationPackage.application_id == application_id,
            )
        )
        if package is None:
            raise ApplicationNotFoundError(application_id)
        return await self._package_response(package)

    async def approve(
        self, *, user_id: int, application_id: int, payload: ApprovePreparationRequest
    ) -> PreparationResponse:
        application = await self._locked_application(user_id=user_id, application_id=application_id)
        await self._require_actionable(application)
        preparation = await self._session.get(
            ApplicationPreparation, application.id, with_for_update=True
        )
        if preparation is None or preparation.version != payload.expected_version:
            raise PreparationVersionConflictError()
        package = await self._session.scalar(
            select(ApplicationPackage).where(
                ApplicationPackage.id == payload.package_id,
                ApplicationPackage.application_id == application.id,
            )
        )
        # Scope package lookup to the already-authorized application.  A missing
        # scoped package is deliberately indistinguishable from a nonexistent
        # package; only an in-scope package with a wrong hash is a conflict.
        if package is None:
            raise ApplicationNotFoundError(application_id)
        if package.content_sha256 != payload.content_sha256:
            raise PackageApprovalConflictError()
        if preparation.reviewable_package_id != package.id:
            raise PackageApprovalConflictError()
        try:
            current_hash = _canonical_hash(
                await self._snapshot(
                    application=application, preparation=preparation, user_id=user_id, strict=True
                )
            )
        except PreparationSelectionInvalidError as exc:
            # A package can remain historical while its mutable selections have
            # drifted.  Approval exposes that as the established 409 package
            # conflict, not a validation detail about the current source.
            raise PackageApprovalConflictError() from exc
        if current_hash != package.content_sha256:
            raise PackageApprovalConflictError()
        preparation.approved_package_id = package.id
        preparation.version += 1
        await self._event(
            application,
            "preparation_approved",
            {"package_id": package.id, "revision": package.revision},
        )
        await self._session.flush()
        return await self._project(
            application=application, preparation=preparation, user_id=user_id
        )

    async def has_history(self, *, application_id: int) -> bool:
        return (
            await self._session.get(ApplicationPreparation, application_id) is not None
            or await self._session.scalar(
                select(ApplicationPackage.id)
                .where(ApplicationPackage.application_id == application_id)
                .limit(1)
            )
            is not None
        )

    async def _application(self, *, user_id: int, application_id: int) -> Application:
        application = await self._session.scalar(
            select(Application).where(
                Application.id == application_id, Application.user_id == user_id
            )
        )
        if application is None:
            raise ApplicationNotFoundError(application_id)
        return application

    async def _locked_application(self, *, user_id: int, application_id: int) -> Application:
        application = await self._session.scalar(
            select(Application)
            .where(Application.id == application_id, Application.user_id == user_id)
            .with_for_update()
        )
        if application is None:
            raise ApplicationNotFoundError(application_id)
        return application

    async def _apply_patch(
        self,
        *,
        application: Application,
        preparation: ApplicationPreparation,
        user_id: int,
        payload: PreparationPatch,
    ) -> set[str]:
        fields = payload.model_fields_set
        changed: set[str] = set()
        if "fit_acknowledged" in fields:
            fit_hash, _ = await self._fit_hash(application, user_id)
            value = fit_hash if payload.fit_acknowledged else None
            if preparation.fit_acknowledged_hash != value:
                preparation.fit_acknowledged_hash = value
                changed.add("fit_acknowledged")
        if "resume_draft_id" in fields:
            if payload.resume_draft_id is None:
                if preparation.resume_draft_id is not None:
                    preparation.resume_draft_id = None
                    preparation.resume_content_hash = None
                    changed.add("resume_draft_id")
            else:
                draft = await self._draft(
                    user_id=user_id, application=application, draft_id=payload.resume_draft_id
                )
                content_hash = _canonical_hash(draft.content)
                if (
                    preparation.resume_draft_id != draft.id
                    or preparation.resume_content_hash != content_hash
                ):
                    preparation.resume_draft_id = draft.id
                    preparation.resume_content_hash = content_hash
                    changed.add("resume_draft_id")
        if "cover_letter_mode" in fields or "cover_letter_id" in fields:
            mode = payload.cover_letter_mode or preparation.cover_letter_mode
            letter_id = (
                payload.cover_letter_id
                if "cover_letter_id" in fields
                else preparation.cover_letter_id
            )
            if mode == "selected":
                if letter_id is None:
                    raise PreparationSelectionInvalidError("A selected cover letter is required")
                letter = await self._letter(
                    user_id=user_id, application=application, letter_id=letter_id
                )
                letter_hash = _canonical_hash(_letter_projection(letter))
                if (
                    preparation.cover_letter_mode,
                    preparation.cover_letter_id,
                    preparation.cover_letter_content_hash,
                ) != (mode, letter.id, letter_hash):
                    (
                        preparation.cover_letter_mode,
                        preparation.cover_letter_id,
                        preparation.cover_letter_content_hash,
                    ) = mode, letter.id, letter_hash
                    changed.add("cover_letter")
            elif mode == "not_needed":
                if (
                    preparation.cover_letter_mode,
                    preparation.cover_letter_id,
                    preparation.cover_letter_content_hash,
                ) != (mode, None, None):
                    (
                        preparation.cover_letter_mode,
                        preparation.cover_letter_id,
                        preparation.cover_letter_content_hash,
                    ) = mode, None, None
                    changed.add("cover_letter")
            elif mode == "unset":
                if (
                    preparation.cover_letter_mode,
                    preparation.cover_letter_id,
                    preparation.cover_letter_content_hash,
                ) != (mode, None, None):
                    (
                        preparation.cover_letter_mode,
                        preparation.cover_letter_id,
                        preparation.cover_letter_content_hash,
                    ) = mode, None, None
                    changed.add("cover_letter")
        if "manual_questions" in fields:
            questions = _questions_for_storage(
                payload.manual_questions or [], preparation.manual_questions
            )
            if questions != preparation.manual_questions:
                preparation.manual_questions = questions
                changed.add("manual_questions")
        return changed

    async def _project(
        self, *, application: Application, preparation: ApplicationPreparation | None, user_id: int
    ) -> PreparationResponse:
        if preparation is None:
            return PreparationResponse(
                application_id=application.id,
                version=0,
                state="not_started",
                fit_acknowledged=False,
                fit_context_available=False,
                resume_draft_id=None,
                resume_current=False,
                cover_letter_mode="unset",
                cover_letter_id=None,
                cover_letter_current=False,
                manual_questions=[],
                approved_package_id=None,
                approval_current=False,
            )
        fit_hash, fit_available = await self._fit_hash(application, user_id)
        fit_current = preparation.fit_acknowledged_hash == fit_hash
        resume_current = await self._resume_current(application, preparation, user_id)
        cover_current = await self._letter_current(application, preparation, user_id)
        questions = [ManualQuestion.model_validate(q) for q in preparation.manual_questions]
        questions_current = all(question.acknowledged for question in questions)
        base_complete = fit_current and resume_current and cover_current and questions_current
        approval_current = False
        if base_complete and preparation.approved_package_id is not None:
            package = await self._session.get(ApplicationPackage, preparation.approved_package_id)
            if package is not None and package.application_id == application.id:
                try:
                    approval_current = package.content_sha256 == _canonical_hash(
                        await self._snapshot(
                            application=application,
                            preparation=preparation,
                            user_id=user_id,
                            strict=True,
                        )
                    )
                except Exception:
                    approval_current = False
        has_choices = bool(
            preparation.fit_acknowledged_hash
            or preparation.resume_draft_id
            or preparation.cover_letter_mode != "unset"
            or questions
        )
        state = (
            "ready"
            if base_complete and approval_current
            else "needs_review"
            if base_complete
            else "in_progress"
            if has_choices
            else "not_started"
        )
        return PreparationResponse(
            application_id=application.id,
            version=preparation.version,
            state=state,
            fit_acknowledged=fit_current,
            fit_context_available=fit_available,
            resume_draft_id=preparation.resume_draft_id,
            resume_current=resume_current,
            cover_letter_mode=preparation.cover_letter_mode,
            cover_letter_id=preparation.cover_letter_id,
            cover_letter_current=cover_current,
            manual_questions=questions,
            approved_package_id=preparation.approved_package_id,
            approval_current=approval_current,
        )

    async def _snapshot(
        self,
        *,
        application: Application,
        preparation: ApplicationPreparation,
        user_id: int,
        strict: bool,
    ) -> dict[str, object]:
        if (
            not preparation.fit_acknowledged_hash
            or not preparation.resume_draft_id
            or preparation.cover_letter_mode == "unset"
        ):
            raise PreparationSelectionInvalidError("Preparation is incomplete")
        fit_hash, _ = await self._fit_hash(application, user_id)
        if preparation.fit_acknowledged_hash != fit_hash:
            raise PreparationSelectionInvalidError("Fit acknowledgement is stale")
        draft = await self._draft(
            user_id=user_id, application=application, draft_id=preparation.resume_draft_id
        )
        if preparation.resume_content_hash != _canonical_hash(draft.content):
            raise PreparationSelectionInvalidError("Selected resume has changed")
        resume = TailoredResume.model_validate(draft.content)
        validated = await self._resume_service.validate_resume_export(
            user_id=user_id, resume=resume, job_repository=self._jobs
        )
        letter_snapshot: dict[str, object]
        if preparation.cover_letter_mode == "not_needed":
            letter_snapshot = {"mode": "not_needed"}
        else:
            if preparation.cover_letter_id is None:
                raise PreparationSelectionInvalidError("Selected cover letter is missing")
            letter = await self._letter(
                user_id=user_id, application=application, letter_id=preparation.cover_letter_id
            )
            if preparation.cover_letter_content_hash != _canonical_hash(_letter_projection(letter)):
                raise PreparationSelectionInvalidError("Selected cover letter has changed")
            letter_snapshot = {"mode": "selected", **_letter_projection(letter)}
        questions = [ManualQuestion.model_validate(q) for q in preparation.manual_questions]
        if not all(question.acknowledged for question in questions):
            raise PreparationSelectionInvalidError("Manual questions need acknowledgement")
        job = await self._jobs.get_by_id(application.job_id)
        if job is None:
            raise ApplicationNotFoundError(application.id)
        return {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "job": {field: getattr(job, field) for field in _FIT_FIELDS},
            "resume": {
                "content": _json_value(validated),
                "provenance": {
                    "source_schema_version": validated.source_schema_version,
                    "source_revision_sha256": validated.source_revision_sha256,
                },
            },
            "cover_letter": letter_snapshot,
            "manual_questions": [{"question": q.question, "answer": q.answer} for q in questions],
        }

    async def _draft(
        self, *, user_id: int, application: Application, draft_id: int
    ) -> TailoredResumeDraft:
        draft = await self._resumes.get_tailored_draft(user_id=user_id, draft_id=draft_id)
        if draft is None:
            raise TailoredResumeDraftNotFoundError(draft_id)
        if draft.job_id != application.job_id:
            raise PreparationSelectionInvalidError("Selected resume belongs to a different job")
        return draft

    async def _letter(
        self, *, user_id: int, application: Application, letter_id: int
    ) -> CoverLetter:
        letter = await self._letters.get(user_id=user_id, cover_letter_id=letter_id)
        if letter is None:
            # CoverLetterNotFound has the established non-disclosing HTTP mapping.
            from app.domain.cover_letters.exceptions import CoverLetterNotFoundError

            raise CoverLetterNotFoundError(letter_id)
        if letter.job_id != application.job_id:
            raise PreparationSelectionInvalidError(
                "Selected cover letter belongs to a different job"
            )
        return letter

    async def _resume_current(
        self, application: Application, preparation: ApplicationPreparation, user_id: int
    ) -> bool:
        if preparation.resume_draft_id is None or preparation.resume_content_hash is None:
            return False
        try:
            draft = await self._draft(
                user_id=user_id, application=application, draft_id=preparation.resume_draft_id
            )
            if preparation.resume_content_hash != _canonical_hash(draft.content):
                return False
            resume = TailoredResume.model_validate(draft.content)
            await self._resume_service.validate_resume_export(
                user_id=user_id, resume=resume, job_repository=self._jobs
            )
            return True
        except Exception:
            return False

    async def _letter_current(
        self, application: Application, preparation: ApplicationPreparation, user_id: int
    ) -> bool:
        if preparation.cover_letter_mode == "not_needed":
            return True
        if preparation.cover_letter_mode != "selected" or preparation.cover_letter_id is None:
            return False
        try:
            letter = await self._letter(
                user_id=user_id, application=application, letter_id=preparation.cover_letter_id
            )
            return preparation.cover_letter_content_hash == _canonical_hash(
                _letter_projection(letter)
            )
        except Exception:
            return False

    async def _fit_hash(self, application: Application, user_id: int) -> tuple[str, bool]:
        job = await self._jobs.get_by_id(application.job_id)
        if job is None:
            raise ApplicationNotFoundError(application.id)
        match = await self._session.scalar(
            select(UserJobMatch).where(
                UserJobMatch.user_id == user_id, UserJobMatch.job_id == application.job_id
            )
        )
        match_content: object = {"availability": "unavailable"}
        if match is not None:
            match_content = _fit_match_projection(match)
        return _canonical_hash(
            {"job": {field: getattr(job, field) for field in _FIT_FIELDS}, "match": match_content}
        ), match is not None

    async def _package_by_key(self, application_id: int, key: str) -> ApplicationPackage | None:
        return cast(
            ApplicationPackage | None,
            await self._session.scalar(
            select(ApplicationPackage).where(
                ApplicationPackage.application_id == application_id,
                ApplicationPackage.idempotency_key == key,
            )
            ),
        )

    async def _package_by_hash(
        self, application_id: int, content_hash: str
    ) -> ApplicationPackage | None:
        return cast(
            ApplicationPackage | None,
            await self._session.scalar(
            select(ApplicationPackage).where(
                ApplicationPackage.application_id == application_id,
                ApplicationPackage.content_sha256 == content_hash,
            )
            ),
        )

    async def _event(
        self, application: Application, event_type: str, metadata: dict[str, object]
    ) -> None:
        self._session.add(
            ApplicationEvent(
                user_id=application.user_id,
                application_id=application.id,
                job_id=application.job_id,
                event_type=event_type,
                metadata_={"source": "application_preparation", **metadata},
            )
        )

    async def _require_actionable(self, application: Application) -> None:
        """Retain Unit 1D's write boundary without changing read visibility."""
        job = await self._jobs.get_by_id(application.job_id)
        if job is None or job.status != "active":
            raise ApplicationJobNotActionableError()

    async def _package_response(self, package: ApplicationPackage) -> ApplicationPackageResponse:
        artifacts = list((await self._session.scalars(
            select(ApplicationArtifact).options(load_only(
                ApplicationArtifact.id, ApplicationArtifact.package_id,
                ApplicationArtifact.document_type, ApplicationArtifact.format,
                ApplicationArtifact.filename, ApplicationArtifact.byte_length,
                ApplicationArtifact.sha256, ApplicationArtifact.validation_state,
                ApplicationArtifact.failure_category, ApplicationArtifact.created_at,
                raiseload=True,
            )).where(ApplicationArtifact.package_id == package.id)
            .order_by(ApplicationArtifact.created_at.desc(), ApplicationArtifact.id.desc())
        )).all())
        return ApplicationPackageResponse(
            id=package.id,
            application_id=package.application_id,
            revision=package.revision,
            content=package.content,
            content_sha256=package.content_sha256,
            schema_version=package.schema_version,
            created_at=package.created_at,
            artifacts=[artifact_summary(row) for row in artifacts],
        )


def _letter_projection(letter: CoverLetter) -> dict[str, object]:
    return {
        "content": letter.content,
        "tone": letter.tone,
        "model": letter.model,
        "is_edited": letter.is_edited,
        "provenance": "user_edited" if letter.is_edited else "generated",
    }


def _questions_for_storage(
    incoming: list[ManualQuestion], previous: list[dict[str, object]]
) -> list[dict[str, object]]:
    values = [question.model_dump(mode="json") for question in incoming]
    payload_size = len(
        json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    if payload_size > 64 * 1024:
        raise PreparationSelectionInvalidError("Manual questions exceed 64 KiB")
    previous_by_id = {str(item.get("id")): item for item in previous}
    stored: list[dict[str, object]] = []
    for value in values:
        item_id = str(value.get("id") or uuid4())
        old = previous_by_id.get(item_id)
        changed = (
            old is None
            or old.get("question") != value["question"]
            or old.get("answer") != value["answer"]
        )
        stored.append(
            {
                "id": item_id,
                "question": value["question"],
                "answer": value["answer"],
                "acknowledged": False if changed else bool(value.get("acknowledged")),
            }
        )
    return stored
