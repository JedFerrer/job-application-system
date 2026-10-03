# ruff: noqa: E501, A002
"""Persistence primitives for immutable application artifacts."""

from __future__ import annotations

from typing import cast

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.applications.artifacts import (
    MAX_APPLICATION_ARTIFACT_BYTES,
    ArtifactFailureCategory,
    ArtifactValidationError,
    preflight_artifact,
)
from app.models.application import Application
from app.models.application_artifact import ApplicationArtifact
from app.models.application_package import ApplicationPackage
from app.models.application_submission_artifact import ApplicationSubmissionArtifact


class ApplicationArtifactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_referenced_by_submission(self, artifact_id: int) -> bool:
        return bool(
            await self._session.scalar(
                select(exists().where(ApplicationSubmissionArtifact.artifact_id == artifact_id))
            )
        )

    async def _lock_owned_application(
        self, *, user_id: int, application_id: int
    ) -> Application | None:
        return cast(
            Application | None,
            await self._session.scalar(
                select(Application)
                .where(Application.id == application_id, Application.user_id == user_id)
                .with_for_update()
            ),
        )

    async def _owned_package(
        self, *, user_id: int, application_id: int, package_id: int
    ) -> ApplicationPackage | None:
        return cast(
            ApplicationPackage | None,
            await self._session.scalar(
                select(ApplicationPackage)
                .join(Application, Application.id == ApplicationPackage.application_id)
                .where(
                    ApplicationPackage.id == package_id,
                    ApplicationPackage.application_id == application_id,
                    Application.user_id == user_id,
                )
            ),
        )

    async def _stored_bytes_for_application(self, *, application_id: int) -> int:
        total = await self._session.scalar(
            select(func.coalesce(func.sum(ApplicationArtifact.byte_length), 0)).where(
                ApplicationArtifact.application_id == application_id
            )
        )
        return int(total or 0)

    async def _existing_identity(
        self, *, package_id: int, document_type: str, format: str, sha256: str
    ) -> ApplicationArtifact | None:
        return cast(
            ApplicationArtifact | None,
            await self._session.scalar(
                select(ApplicationArtifact).where(
                    ApplicationArtifact.package_id == package_id,
                    ApplicationArtifact.document_type == document_type,
                    ApplicationArtifact.format == format,
                    ApplicationArtifact.sha256 == sha256,
                )
            ),
        )

    async def create_artifact(
        self,
        *,
        user_id: int,
        application_id: int,
        package_id: int,
        document_type: str,
        format: str,
        filename: str,
        content: bytes,
        exporter_version: str,
        expected_text_sha256: str,
        validation_policy_version: str,
    ) -> ApplicationArtifact:
        artifact, _ = await self.create_artifact_with_status(
            user_id=user_id,
            application_id=application_id,
            package_id=package_id,
            document_type=document_type,
            format=format,
            filename=filename,
            content=content,
            exporter_version=exporter_version,
            expected_text_sha256=expected_text_sha256,
            validation_policy_version=validation_policy_version,
        )
        return artifact

    async def create_artifact_with_status(
        self,
        *,
        user_id: int,
        application_id: int,
        package_id: int,
        document_type: str,
        format: str,
        filename: str,
        content: bytes,
        exporter_version: str,
        expected_text_sha256: str,
        validation_policy_version: str,
    ) -> tuple[ApplicationArtifact, bool]:
        """Lock, deduplicate, enforce quota, and insert in the caller's transaction."""
        preflight = preflight_artifact(content=content, document_type=document_type, format=format)  # type: ignore[arg-type]
        application = await self._lock_owned_application(
            user_id=user_id, application_id=application_id
        )
        if (
            application is None
            or await self._owned_package(
                user_id=user_id, application_id=application_id, package_id=package_id
            )
            is None
        ):
            raise ArtifactValidationError(ArtifactFailureCategory.CONTENT_MISMATCH)
        existing = await self._existing_identity(
            package_id=package_id,
            document_type=document_type,
            format=format,
            sha256=preflight.sha256,
        )
        if existing is not None:
            return existing, False
        total = await self._stored_bytes_for_application(application_id=application_id)
        if total + preflight.byte_length > MAX_APPLICATION_ARTIFACT_BYTES:
            raise ArtifactValidationError(ArtifactFailureCategory.QUOTA_EXCEEDED)
        artifact = ApplicationArtifact(
            user_id=user_id,
            application_id=application_id,
            package_id=package_id,
            document_type=document_type,
            format=format,
            filename=filename,
            content_type=preflight.content_type,
            byte_length=preflight.byte_length,
            sha256=preflight.sha256,
            exporter_version=exporter_version,
            expected_text_sha256=expected_text_sha256,
            validation_policy_version=validation_policy_version,
            validation_state="queued",
            validation_report={},
            content=content,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(artifact)
                await self._session.flush()
        except IntegrityError as exc:
            cause = exc.orig
            if (
                getattr(cause, "sqlstate", None) != "23505"
                or getattr(getattr(cause, "diag", None), "constraint_name", None)
                != "uq_application_artifact_identity"
            ):
                raise
            existing = await self._existing_identity(
                package_id=package_id,
                document_type=document_type,
                format=format,
                sha256=preflight.sha256,
            )
            if existing is None:
                raise
            return existing, False
        return artifact, True
