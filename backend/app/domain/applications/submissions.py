"""Owned, explicit confirmation and deletion of submitted material."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime

from sqlalchemy import delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.domain.applications.artifact_lifecycle import artifact_summary
from app.domain.applications.schemas import (
    ArtifactSummary,
    SubmissionConfirmRequest,
    SubmissionListResponse,
    SubmissionResponse,
)
from app.models.application import Application
from app.models.application_artifact import ApplicationArtifact
from app.models.application_attempt import ApplicationAttempt
from app.models.application_event import ApplicationEvent
from app.models.application_package import ApplicationPackage
from app.models.application_submission import ApplicationSubmission
from app.models.application_submission_artifact import ApplicationSubmissionArtifact
from app.models.user import User


class SubmissionNotFoundError(Exception):
    pass


class SubmissionConflictError(Exception):
    pass


class SubmissionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def confirm(
        self, *, user_id: int, application_id: int,
        request: SubmissionConfirmRequest, idempotency_key: str,
    ) -> tuple[SubmissionResponse, bool]:
        # The key is unique per user, including across different applications.
        # Locking that user also makes concurrent retries observe one result.
        owner = await self._session.scalar(select(User.id).where(
            User.id == user_id
        ).with_for_update())
        if owner is None:
            raise SubmissionNotFoundError
        # Lock the application so the status update shares this transaction.
        application = await self._session.scalar(
            select(Application).where(
                Application.id == application_id, Application.user_id == user_id
            ).with_for_update()
        )
        if application is None:
            raise SubmissionNotFoundError

        existing = await self._session.scalar(select(ApplicationSubmission).where(
            ApplicationSubmission.user_id == user_id,
            ApplicationSubmission.idempotency_key == idempotency_key,
        ))
        if existing is not None:
            if not await self._same_confirmation(existing, application_id, request):
                raise SubmissionConflictError("Idempotency key was used for another confirmation")
            return await self.detail(
                user_id=user_id, application_id=application_id, submission_id=existing.id,
            ), False

        if request.attempt_id is not None:
            attempt = await self._session.scalar(select(ApplicationAttempt).where(
                ApplicationAttempt.id == request.attempt_id,
                ApplicationAttempt.user_id == user_id,
                ApplicationAttempt.application_id == application_id,
            ))
            if attempt is None:
                raise SubmissionNotFoundError
            prior_attempt = await self._session.scalar(select(ApplicationSubmission.id).where(
                ApplicationSubmission.attempt_id == request.attempt_id
            ))
            if prior_attempt is not None:
                raise SubmissionConflictError("Attempt already has a confirmed submission")
            if request.package_id is not None and attempt.package_id != request.package_id:
                raise SubmissionConflictError("Selected package differs from the attempt")

        if request.package_id is not None:
            package = await self._session.scalar(select(ApplicationPackage.id).where(
                ApplicationPackage.id == request.package_id,
                ApplicationPackage.application_id == application_id,
            ))
            if package is None:
                raise SubmissionNotFoundError
            if request.artifact_ids:
                owned_ids = set((await self._session.scalars(select(ApplicationArtifact.id).where(
                    ApplicationArtifact.id.in_(request.artifact_ids),
                    ApplicationArtifact.user_id == user_id,
                    ApplicationArtifact.application_id == application_id,
                    ApplicationArtifact.package_id == request.package_id,
                ))).all())
                if owned_ids != set(request.artifact_ids):
                    raise SubmissionNotFoundError

        submission = ApplicationSubmission(
            user_id=user_id, application_id=application_id,
            attempt_id=request.attempt_id, package_id=request.package_id,
            material_choice=request.material_choice,
            provenance="user_confirmed_materials",
            submitted_at=request.submitted_at or datetime.now(UTC),
            note=request.note, idempotency_key=idempotency_key,
        )
        self._session.add(submission)
        await self._session.flush()
        for artifact_id in request.artifact_ids:
            self._session.add(ApplicationSubmissionArtifact(
                submission_id=submission.id, artifact_id=artifact_id,
            ))

        # This event is evidence of confirmation and intentionally outlives
        # explicit deletion of the submission and its private note.
        self._session.add(ApplicationEvent(
            user_id=user_id, application_id=application_id,
            attempt_id=request.attempt_id, job_id=application.job_id,
            event_type="submission_confirmed",
            metadata_={
                "submission_id": submission.id,
                "material_choice": submission.material_choice,
                "provenance": submission.provenance,
                "submitted_at_explicit": request.submitted_at is not None,
            },
        ))
        if application.status == "saved":
            application.status = "applied"
        await self._session.flush()
        return await self.detail(
            user_id=user_id, application_id=application_id, submission_id=submission.id,
        ), True

    async def list_submissions(
        self, *, user_id: int, application_id: int,
        limit: int = 25, cursor: str | None = None,
    ) -> SubmissionListResponse:
        await self._owned_application(user_id, application_id)
        boundary = _decode_cursor(cursor) if cursor is not None else None
        query = select(ApplicationSubmission).where(
            ApplicationSubmission.user_id == user_id,
            ApplicationSubmission.application_id == application_id,
        )
        if boundary is not None:
            query = query.where(tuple_(
                ApplicationSubmission.created_at, ApplicationSubmission.id,
            ) < boundary)
        rows = list((await self._session.scalars(query.order_by(
            ApplicationSubmission.created_at.desc(), ApplicationSubmission.id.desc(),
        ).limit(limit + 1))).all())
        has_more = len(rows) > limit
        rows = rows[:limit]
        links = await self._artifact_ids_for_submissions([row.id for row in rows])
        reported = await self._reported_time_for_submissions(
            user_id=user_id, application_id=application_id,
            submission_ids=[row.id for row in rows],
        )
        next_cursor = _encode_cursor(rows[-1]) if has_more and rows else None
        return SubmissionListResponse(
            items=[self._response(row, links.get(row.id, []), reported[row.id]) for row in rows],
            next_cursor=next_cursor,
        )

    async def detail(
        self, *, user_id: int, application_id: int, submission_id: int,
    ) -> SubmissionResponse:
        row = await self._owned_submission(user_id, application_id, submission_id)
        artifacts = list((await self._session.scalars(
            select(ApplicationArtifact).join(
                ApplicationSubmissionArtifact,
                ApplicationSubmissionArtifact.artifact_id == ApplicationArtifact.id,
            ).where(ApplicationSubmissionArtifact.submission_id == row.id).options(
                load_only(
                    ApplicationArtifact.id, ApplicationArtifact.package_id,
                    ApplicationArtifact.document_type, ApplicationArtifact.format,
                    ApplicationArtifact.filename, ApplicationArtifact.byte_length,
                    ApplicationArtifact.sha256, ApplicationArtifact.validation_state,
                    ApplicationArtifact.failure_category, ApplicationArtifact.created_at,
                )
            ).order_by(ApplicationArtifact.id)
        )).all())
        reported = await self._reported_time_for_submissions(
            user_id=user_id, application_id=application_id, submission_ids=[row.id],
        )
        return self._response(
            row, [artifact.id for artifact in artifacts], reported[row.id],
            artifacts=[artifact_summary(artifact) for artifact in artifacts],
        )

    async def delete(
        self, *, user_id: int, application_id: int, submission_id: int,
    ) -> None:
        await self._owned_submission(user_id, application_id, submission_id)
        # Database FKs cascade submission-artifact links and interview prep.
        # Packages, artifacts, status and chronological events are independent.
        await self._session.execute(delete(ApplicationSubmission).where(
            ApplicationSubmission.id == submission_id,
            ApplicationSubmission.user_id == user_id,
            ApplicationSubmission.application_id == application_id,
        ))
        await self._session.flush()

    async def _owned_application(self, user_id: int, application_id: int) -> None:
        found = await self._session.scalar(select(Application.id).where(
            Application.id == application_id, Application.user_id == user_id,
        ))
        if found is None:
            raise SubmissionNotFoundError

    async def _owned_submission(
        self, user_id: int, application_id: int, submission_id: int,
    ) -> ApplicationSubmission:
        row = await self._session.scalar(select(ApplicationSubmission).where(
            ApplicationSubmission.id == submission_id,
            ApplicationSubmission.user_id == user_id,
            ApplicationSubmission.application_id == application_id,
        ))
        if row is None:
            raise SubmissionNotFoundError
        return row

    async def _artifact_ids_for_submissions(
        self, submission_ids: list[int],
    ) -> dict[int, list[int]]:
        if not submission_ids:
            return {}
        links: dict[int, list[int]] = {}
        pairs = (await self._session.execute(select(
            ApplicationSubmissionArtifact.submission_id,
            ApplicationSubmissionArtifact.artifact_id,
        ).where(ApplicationSubmissionArtifact.submission_id.in_(submission_ids)).order_by(
            ApplicationSubmissionArtifact.submission_id,
            ApplicationSubmissionArtifact.artifact_id,
        ))).all()
        for submission_id, artifact_id in pairs:
            links.setdefault(submission_id, []).append(artifact_id)
        return links

    async def _reported_time_for_submissions(
        self, *, user_id: int, application_id: int, submission_ids: list[int],
    ) -> dict[int, bool]:
        if not submission_ids:
            return {}
        metadata_rows = (await self._session.scalars(select(
            ApplicationEvent.metadata_,
        ).where(
            ApplicationEvent.user_id == user_id,
            ApplicationEvent.application_id == application_id,
            ApplicationEvent.event_type == "submission_confirmed",
            ApplicationEvent.metadata_["submission_id"].as_integer().in_(submission_ids),
        ))).all()
        reported: dict[int, bool] = {}
        for metadata in metadata_rows:
            submission_id = metadata.get("submission_id")
            explicit = metadata.get("submitted_at_explicit")
            if type(submission_id) is not int or type(explicit) is not bool:
                raise RuntimeError("Submission confirmation provenance is missing")
            reported[submission_id] = explicit
        if set(reported) != set(submission_ids):
            raise RuntimeError("Submission confirmation provenance is missing")
        return reported

    def _response(
        self, row: ApplicationSubmission, artifact_ids: list[int],
        submitted_at_is_user_reported: bool,
        *, artifacts: list[ArtifactSummary] | None = None,
    ) -> SubmissionResponse:
        return SubmissionResponse(
            id=row.id, application_id=row.application_id,
            attempt_id=row.attempt_id, package_id=row.package_id,
            artifact_ids=artifact_ids, artifacts=artifacts or [],
            material_choice=row.material_choice, provenance=row.provenance,
            submitted_at=row.submitted_at,
            submitted_at_is_user_reported=submitted_at_is_user_reported,
            note=row.note, created_at=row.created_at,
        )

    async def _same_confirmation(
        self, existing: ApplicationSubmission, application_id: int,
        request: SubmissionConfirmRequest,
    ) -> bool:
        event = await self._session.scalar(select(ApplicationEvent).where(
            ApplicationEvent.user_id == existing.user_id,
            ApplicationEvent.application_id == existing.application_id,
            ApplicationEvent.event_type == "submission_confirmed",
            ApplicationEvent.metadata_["submission_id"].as_integer() == existing.id,
        ))
        if event is None or not isinstance(event.metadata_.get("submitted_at_explicit"), bool):
            return False
        artifact_ids = (await self._artifact_ids_for_submissions([existing.id])).get(
            existing.id, []
        )
        return (
            existing.application_id == application_id
            and existing.attempt_id == request.attempt_id
            and existing.package_id == request.package_id
            and existing.material_choice == request.material_choice
            and existing.note == request.note
            and event.metadata_["submitted_at_explicit"] == (request.submitted_at is not None)
            and (request.submitted_at is None or existing.submitted_at == request.submitted_at)
            and artifact_ids == sorted(request.artifact_ids)
        )


def _encode_cursor(row: ApplicationSubmission) -> str:
    raw = f"v1|{row.created_at.isoformat()}|{row.id}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        raw = base64.b64decode(cursor, altchars=b"-_", validate=True).decode("ascii")
        version, timestamp, row_id = raw.split("|")
        value = datetime.fromisoformat(timestamp)
        number = int(row_id)
        if version != "v1" or value.tzinfo is None or number <= 0:
            raise ValueError
        return value, number
    except (ValueError, UnicodeDecodeError, binascii.Error):
        raise ValueError("Invalid submission cursor") from None
