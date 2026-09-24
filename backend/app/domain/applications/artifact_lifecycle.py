# ruff: noqa: E501, A002
"""Owned artifact admission, retrieval, and fenced background validation."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.domain.applications.artifact_repository import ApplicationArtifactRepository
from app.domain.applications.artifacts import (
    VALIDATION_POLICY_VERSION,
    ArtifactDocumentType,
    ArtifactFailureCategory,
    ArtifactFormat,
    ArtifactValidationError,
    ArtifactValidationReport,
    canonical_filename,
    expected_text_sha256,
    package_expected_text,
    validate_in_isolated_process,
)
from app.domain.applications.schemas import ArtifactDetail, ArtifactSummary, ArtifactUploadResponse
from app.infrastructure.database import async_session_factory
from app.infrastructure.redis_client import get_redis
from app.infrastructure.task_store import TaskStore
from app.models.application import Application
from app.models.application_artifact import ApplicationArtifact
from app.models.application_package import ApplicationPackage

STALE_AFTER = timedelta(seconds=60)
TERMINAL_STATES = frozenset({"validated", "warning", "failed", "unavailable"})
logger = get_logger("artifact_lifecycle")


class ArtifactNotFoundError(Exception):
    pass


class ArtifactConflictError(Exception):
    pass


class ArtifactTaskAdmissionError(Exception):
    pass


async def _new_task(task_store: TaskStore, user_id: int) -> str:
    try:
        return await task_store.create("artifact_validation", user_id=user_id)
    except Exception:
        raise ArtifactTaskAdmissionError from None


def artifact_summary(row: ApplicationArtifact) -> ArtifactSummary:
    return ArtifactSummary(
        id=row.id, package_id=row.package_id, document_type=row.document_type,
        format=row.format, filename=row.filename, byte_length=row.byte_length,
        sha256=row.sha256, validation_state=row.validation_state,
        failure_category=row.failure_category, created_at=row.created_at,
    )


def artifact_detail(row: ApplicationArtifact) -> ArtifactDetail:
    return ArtifactDetail(
        **artifact_summary(row).model_dump(), application_id=row.application_id,
        content_type=row.content_type, exporter_version=row.exporter_version,
        expected_text_sha256=row.expected_text_sha256,
        validation_policy_version=row.validation_policy_version,
        validation_report=row.validation_report or None, task_id=row.task_id,
    )


class ArtifactLifecycleService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = ApplicationArtifactRepository(session)

    async def owned_package(self, *, user_id: int, application_id: int, package_id: int) -> ApplicationPackage:
        package = await self._session.scalar(
            select(ApplicationPackage).join(Application, Application.id == ApplicationPackage.application_id).where(
                Application.id == application_id, Application.user_id == user_id,
                ApplicationPackage.id == package_id, ApplicationPackage.application_id == application_id,
            )
        )
        if package is None:
            raise ArtifactNotFoundError
        return package

    async def create(
        self, *, user_id: int, application_id: int, package_id: int,
        document_type: str, format: str, filename: str, content: bytes,
        exporter_version: str, task_store: TaskStore,
    ) -> tuple[ArtifactUploadResponse, bool]:
        package = await self.owned_package(user_id=user_id, application_id=application_id, package_id=package_id)
        expected_hash = expected_text_sha256(package.content, document_type)  # type: ignore[arg-type]
        safe_filename = canonical_filename(filename[:240], document_type, format)  # type: ignore[arg-type]
        row, created = await self._repository.create_artifact_with_status(
            user_id=user_id, application_id=application_id, package_id=package_id,
            document_type=document_type, format=format, filename=safe_filename,
            content=content, exporter_version=exporter_version,
            expected_text_sha256=expected_hash,
            validation_policy_version=VALIDATION_POLICY_VERSION,
        )
        if created:
            row.task_id = await _new_task(task_store, user_id)
            row.validation_attempt = 1
            row.validation_state_changed_at = datetime.now(UTC)
            await self._session.flush()
        return ArtifactUploadResponse(artifact=artifact_detail(row), existing=not created), created

    async def owned_artifact(self, *, user_id: int, application_id: int, artifact_id: int, lock: bool = False) -> ApplicationArtifact:
        query = select(ApplicationArtifact).join(Application, Application.id == ApplicationArtifact.application_id).where(
            ApplicationArtifact.id == artifact_id, ApplicationArtifact.application_id == application_id,
            ApplicationArtifact.user_id == user_id, Application.user_id == user_id,
        )
        if lock:
            query = query.with_for_update(of=ApplicationArtifact)
        row = cast(ApplicationArtifact | None, await self._session.scalar(query))
        if row is None:
            raise ArtifactNotFoundError
        return row

    async def detail(self, *, user_id: int, application_id: int, artifact_id: int) -> ArtifactDetail:
        return artifact_detail(await self.owned_artifact(user_id=user_id, application_id=application_id, artifact_id=artifact_id))

    async def retry(self, *, user_id: int, application_id: int, artifact_id: int, task_store: TaskStore) -> tuple[ArtifactUploadResponse, int]:
        row = await self.owned_artifact(user_id=user_id, application_id=application_id, artifact_id=artifact_id, lock=True)
        if row.validation_state in {"validated", "warning"}:
            raise ArtifactConflictError
        if row.validation_state in {"queued", "validating"} and datetime.now(UTC) - row.validation_state_changed_at < STALE_AFTER:
            raise ArtifactConflictError
        row.task_id = await _new_task(task_store, user_id)
        row.validation_attempt += 1
        row.validation_state = "queued"
        row.validation_state_changed_at = datetime.now(UTC)
        row.validation_report = {}
        row.failure_category = None
        await self._session.flush()
        return ArtifactUploadResponse(artifact=artifact_detail(row), existing=False), row.validation_attempt

    async def delete(self, *, user_id: int, application_id: int, artifact_id: int) -> None:
        row = await self.owned_artifact(user_id=user_id, application_id=application_id, artifact_id=artifact_id, lock=True)
        if row.validation_state not in TERMINAL_STATES:
            raise ArtifactConflictError
        await self._session.delete(row)
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class _ValidationSnapshot:
    content: bytes
    format: ArtifactFormat
    document_type: ArtifactDocumentType
    policy_version: str
    package_content: dict[str, object] | None


def _failure_report(snapshot: _ValidationSnapshot, category: ArtifactFailureCategory) -> ArtifactValidationReport:
    state = "unavailable" if category == ArtifactFailureCategory.PARSER_UNAVAILABLE else "failed"
    return ArtifactValidationReport(
        policy_version=snapshot.policy_version, format=snapshot.format,
        state=state, checks=[], failure_category=category,
    )


async def run_artifact_validation_background(*, artifact_id: int, task_id: str, validation_attempt: int) -> None:
    """Claim and complete only the persisted attempt; never pass request bytes here."""
    store = TaskStore(get_redis())
    async with async_session_factory() as session:
        claimed = await session.execute(
            update(ApplicationArtifact).where(
                ApplicationArtifact.id == artifact_id,
                ApplicationArtifact.task_id == task_id,
                ApplicationArtifact.validation_attempt == validation_attempt,
                ApplicationArtifact.validation_state == "queued",
            ).values(validation_state="validating", validation_state_changed_at=datetime.now(UTC)).returning(ApplicationArtifact.id)
        )
        if claimed.scalar_one_or_none() is None:
            await session.rollback()
            return
        await session.commit()
    try:
        await store.update(task_id, status="running", message="Validating artifact", artifact_id=artifact_id)
    except Exception:
        logger.warning("artifact.task_progress_unavailable", artifact_id=artifact_id)

    # Copy the exact stored artifact and its immutable package into plain values.
    # The session (and its read transaction) closes before parser execution.
    async with async_session_factory() as session:
        row = await session.get(ApplicationArtifact, artifact_id)
        if row is None or row.task_id != task_id or row.validation_attempt != validation_attempt:
            return
        package = await session.get(ApplicationPackage, row.package_id)
        snapshot = _ValidationSnapshot(
            content=row.content, format=cast("ArtifactFormat", row.format),
            document_type=cast("ArtifactDocumentType", row.document_type),
            policy_version=row.validation_policy_version,
            package_content=deepcopy(package.content) if package is not None else None,
        )
        await session.rollback()

    try:
        if snapshot.package_content is None:
            raise ArtifactValidationError(ArtifactFailureCategory.CONTENT_MISMATCH)
        expected = package_expected_text(snapshot.package_content, snapshot.document_type)
        report = await asyncio.to_thread(
            validate_in_isolated_process, content=snapshot.content, format=snapshot.format,
            expected=expected, document_type=snapshot.document_type,
        )
    except ArtifactValidationError as exc:
        report = _failure_report(snapshot, exc.category)
    except Exception:
        report = _failure_report(snapshot, ArtifactFailureCategory.PARSER_UNAVAILABLE)

    async with async_session_factory() as session:
        try:
            written = await session.execute(
                update(ApplicationArtifact).where(
                    ApplicationArtifact.id == artifact_id,
                    ApplicationArtifact.task_id == task_id,
                    ApplicationArtifact.validation_attempt == validation_attempt,
                    ApplicationArtifact.validation_state == "validating",
                ).values(
                    validation_state=report.state.value,
                    validation_report=report.model_dump(mode="json"),
                    failure_category=report.failure_category.value if report.failure_category else None,
                    validation_state_changed_at=datetime.now(UTC),
                ).returning(ApplicationArtifact.id)
            )
            if written.scalar_one_or_none() is None:
                await session.rollback()
                return
            await session.commit()
        except Exception:
            await session.rollback()
            try:
                await store.update(task_id, status="failed", message="Artifact result could not be saved; check artifact status before retrying", artifact_id=artifact_id)
            except Exception:
                logger.warning("artifact.task_failure_update_unavailable", artifact_id=artifact_id)
            return
    try:
        await store.update(
            task_id, status="done" if report.state in {"validated", "warning"} else "failed",
            message="Artifact validation complete" if report.state in {"validated", "warning"} else "Artifact validation unavailable",
            artifact_id=artifact_id,
        )
    except Exception:
        logger.warning("artifact.task_terminal_update_unavailable", artifact_id=artifact_id)
