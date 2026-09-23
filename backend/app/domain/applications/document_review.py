# ruff: noqa: E501, E702
"""Owned orchestration for bounded, advisory immutable-package reviews."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal, cast
from uuid import uuid4

from openai import APIStatusError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.domain.applications.preparation import ApplicationPreparationService
from app.domain.applications.review_contract import REVIEW_CATEGORY_CONTRACTS
from app.domain.applications.review_prompts import (
    MAX_FINDINGS,
    MAX_REVIEW_INPUT_CHARS,
    REVIEW_POLICY_VERSION,
    corrective_review_messages,
    review_messages,
    schema_corrective_review_messages,
)
from app.domain.applications.schemas import (
    DocumentReviewFinding,
    DocumentReviewQueuedResponse,
    DocumentReviewResponse,
    ProviderCrossDocumentFindingV10,
    ProviderDocumentReviewResponseV10,
    ProviderFindingV10,
    ProviderJobTargetFindingV10,
    ProviderSingleTargetFindingV10,
    ReviewAcknowledgmentItem,
    ReviewAcknowledgmentRequest,
)
from app.infrastructure.database import async_session_factory
from app.infrastructure.llm_client import (
    LLMUnavailableError,
    classify_provider_failure,
    llm_chat_completion,
)
from app.infrastructure.llm_utils import strip_code_fences
from app.infrastructure.redis_client import get_redis
from app.infrastructure.task_store import TaskStore
from app.models.application import Application
from app.models.application_package import ApplicationPackage
from app.models.application_preparation import ApplicationPreparation
from app.models.application_review import ApplicationDocumentReview, ApplicationReviewAcknowledgment

_REVIEW_TIMEOUT_SECONDS = 180
_ACTIVE_REVIEW_UNIQUE_CONSTRAINT = "uq_application_review_active"
_UNIQUE_VIOLATION_SQLSTATE = "23505"

logger = get_logger(__name__)

_STAGE_PROVIDER_RESPONSE_EXTRACTION = "provider_response_extraction"
_STAGE_RESPONSE_JSON_PARSE = "response_json_parse"
_STAGE_RESPONSE_SCHEMA_VALIDATION = "response_schema_validation"
_STAGE_EVIDENCE_VALIDATION = "evidence_validation"
_STAGE_CANONICAL_VALIDATION = "canonical_validation"
_STAGE_COMPLETION_PERSISTENCE = "completion_persistence"
_STAGE_TASK_COMPLETION = "task_completion"
_STAGE_WORKER_UNKNOWN = "worker_unknown"
_STAGE_PROVIDER_CALL = "provider_call"
_STAGE_CORRECTIVE_REGENERATION = "corrective_regeneration"
_DIAGNOSTIC_VERSION = "document-review-diagnostic-v1"
_MAX_DIAGNOSTIC_GENERATIONS = 2
_CORRECTIVE_POINTER_FAILURE_KINDS = {
    "missing_key", "invalid_array_index", "array_index_out_of_range",
}
_CORRECTIVE_V6_EVIDENCE_REASONS = {
    "invalid_evidence_id", "duplicate_evidence_id", "invalid_evidence_document",
    "duplicate_evidence_role", "missing_required_evidence",
}
_EVIDENCE_ROOTS = ("cover_letter", "job", "resume")
_MAX_NORMALIZED_SCHEMA_ISSUES = 8
_KNOWN_PROVIDER_FIELD_NAMES = {
    "id", "category", "severity", "rationale", "evidence_ids", "proposed_action",
    "target_evidence_id", "resume_evidence_id", "cover_letter_evidence_id", "job_evidence_id",
    "evidence", "role", "document", "locator", "excerpt",
}
_SCHEMA_ISSUE_TYPES = {
    "missing": "missing_required_field",
    "extra_forbidden": "extra_forbidden_field",
    "literal_error": "invalid_literal",
    "string_too_short": "too_short",
    "list_too_short": "too_short",
    "string_too_long": "too_long",
    "list_too_long": "too_long",
    "model_type": "invalid_object_shape",
    "dict_type": "invalid_object_shape",
    "string_pattern_mismatch": "invalid_evidence_id_namespace",
}
_PROVIDER_UNION_BRANCHES = {
    "unsupported_claim", "cross_document_mismatch", "job_document_mismatch",
    "omitted_verified_evidence", "repetition", "clarity", "keyword_stuffing",
}
_PROVIDER_EVIDENCE_ID_PREFIXES = {
    "job": "job", "resume": "resume", "cover_letter": "cover",
}

_PointerFailureKind = Literal[
    "missing_leading_slash",
    "missing_key",
    "invalid_array_index",
    "array_index_out_of_range",
    "traversal_type_mismatch",
    "resolved_null",
    "unknown_pointer_failure",
]
_PointerContainerKind = Literal["object", "array", "scalar", "null"]
_LocatorRole = Literal["finding", "evidence"]


@dataclass(frozen=True)
class _JsonPointerResolution:
    """Content-free result from the internal strict JSON Pointer resolver.

    ``pointer_depth`` is the number of decoded path segments after the leading
    slash. ``failing_segment_position`` is the zero-based traversal-segment
    index, or ``None`` when there is no leading slash.
    """

    value: object | None
    failure_kind: _PointerFailureKind | None
    pointer_depth: int
    failing_segment_position: int | None
    container_kind: _PointerContainerKind | None


class _SchemaValidationError(ValueError):
    """A fixed internal marker for a non-Pydantic response-shape rejection."""


class _EvidenceValidationError(ValueError):
    """An existing evidence-contract rejection with a content-free reason code."""

    def __init__(self, reason: str, *, structural_metadata: dict[str, object] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.structural_metadata = structural_metadata


@dataclass(frozen=True)
class _EvidenceCatalogEntry:
    evidence_id: str
    document: Literal["job", "resume", "cover_letter"]
    locator: str
    text: str


def _is_corrective_regeneration_eligible(exc: _EvidenceValidationError) -> bool:
    """Keep v6 semantic regeneration limited to provider-correctable references."""
    return exc.reason in _CORRECTIVE_V6_EVIDENCE_REASONS


def _normalized_schema_issue_type(error_type: object) -> str:
    error_name = str(error_type)
    if error_name in _SCHEMA_ISSUE_TYPES:
        return _SCHEMA_ISSUE_TYPES[error_name]
    if error_name.endswith("_type") or error_name in {"string_type", "list_type", "int_type"}:
        return "invalid_field_type"
    return "invalid_field_type"


def _normalized_schema_field_path(location: object) -> str:
    parts: list[str] = []
    for segment in location if isinstance(location, tuple) else ():
        if isinstance(segment, int):
            parts.append("*")
        elif segment in _PROVIDER_UNION_BRANCHES:
            continue
        elif segment == "findings" or segment in _KNOWN_PROVIDER_FIELD_NAMES:
            parts.append(cast(str, segment))
        else:
            parts.append("<unknown_field>")
    return ".".join(parts) if parts else "<unknown_field>"


def _normalize_schema_validation_issues(exc: ValidationError) -> tuple[dict[str, str], ...]:
    """Return a bounded, content-free summary suitable only for transient feedback."""
    normalized = {
        (
            _normalized_schema_issue_type(error.get("type")),
            _normalized_schema_field_path(error.get("loc")),
        )
        for error in exc.errors(include_input=False)
    }
    return tuple(
        {"schema_issue_type": issue_type, "field_path": field_path}
        for issue_type, field_path in sorted(normalized)[:_MAX_NORMALIZED_SCHEMA_ISSUES]
    )


def _log_schema_validation_failure(
    *, generation: int, exc: ValidationError, normalized_schema_issues: tuple[dict[str, str], ...],
) -> None:
    """Emit only the allowlisted structural detail for a provider schema rejection."""
    logger.warning(
        "document_review_schema_validation_failed",
        generation=generation,
        validation_error_count=exc.error_count(),
        normalized_schema_issues=normalized_schema_issues,
    )


def _log_canonical_validation_failure(*, generation: int, exc: BaseException) -> None:
    """Emit only fixed metadata for a server-side canonical validation failure."""
    logger.warning(
        "document_review_canonical_validation_failed",
        generation=generation,
        exception_class=type(exc).__name__,
        validation_error_count=exc.error_count() if isinstance(exc, ValidationError) else 0,
    )


def _is_active_review_unique_violation(exc: IntegrityError) -> bool:
    """Return whether ``exc`` is the active-review admission race we recover."""
    pending: list[BaseException] = [exc]
    visited: set[int] = set()
    has_unique_sqlstate = False
    has_active_review_constraint = False

    while pending and len(visited) < 4:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        sqlstate = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        has_unique_sqlstate = has_unique_sqlstate or sqlstate == _UNIQUE_VIOLATION_SQLSTATE
        has_active_review_constraint = has_active_review_constraint or (
            getattr(current, "constraint_name", None) == _ACTIVE_REVIEW_UNIQUE_CONSTRAINT
        )
        for related in (getattr(current, "orig", None), current.__cause__, current.__context__):
            if isinstance(related, BaseException):
                pending.append(related)

    return has_unique_sqlstate and has_active_review_constraint


class ReviewNotFoundError(Exception):
    pass


class ReviewConflictError(Exception):
    pass


class DocumentReviewService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(
        self, *, user_id: int, application_id: int, package_id: int, idempotency_key: str,
        rerun: bool, task_store: TaskStore,
    ) -> DocumentReviewResponse | DocumentReviewQueuedResponse:
        package = await self._current_owned_package(user_id=user_id, application_id=application_id, package_id=package_id)
        await self._validate_package(package)
        existing = await self._session.scalar(select(ApplicationDocumentReview).where(
            ApplicationDocumentReview.user_id == user_id,
            ApplicationDocumentReview.package_id == package.id,
            ApplicationDocumentReview.idempotency_key == idempotency_key,
        ))
        if existing is not None:
            if existing.package_content_sha256 != package.content_sha256:
                raise ReviewConflictError("Idempotency key was already used for different review content")
            if existing.state in {"queued", "running"}:
                return DocumentReviewQueuedResponse(task_id=existing.task_id, review_id=existing.id, state=existing.state, existing=True)
            return await self._response(existing)
        active = await self._active(
            user_id=user_id, application_id=application_id, package=package
        )
        if active is not None:
            return DocumentReviewQueuedResponse(task_id=active.task_id, review_id=active.id, state=active.state, existing=True)
        cached = await self._cached(user_id=user_id, package=package)
        if cached is not None and not rerun:
            return await self._response(cached)
        review = ApplicationDocumentReview(
            user_id=user_id, application_id=application_id, package_id=package.id,
            package_content_sha256=package.content_sha256, review_policy_version=REVIEW_POLICY_VERSION,
            idempotency_key=idempotency_key, task_id=str(uuid4()),
        )
        try:
            async with self._session.begin_nested():
                self._session.add(review)
                await self._session.flush()
        except IntegrityError as exc:
            if not _is_active_review_unique_violation(exc):
                raise
            active = await self._active(
                user_id=user_id, application_id=application_id, package=package
            )
            if active is None:
                raise
            return DocumentReviewQueuedResponse(
                task_id=active.task_id, review_id=active.id, state=active.state, existing=True
            )
        task_id = await task_store.create("document_review", user_id=user_id)
        review.task_id = task_id
        await self._session.flush()
        await task_store.update(task_id, message="Document review queued", review_id=review.id)
        return DocumentReviewQueuedResponse(task_id=task_id, review_id=review.id, state="queued")

    async def get(self, *, user_id: int, application_id: int, package_id: int, review_id: str) -> DocumentReviewResponse:
        review = await self._session.scalar(select(ApplicationDocumentReview).where(
            ApplicationDocumentReview.id == review_id, ApplicationDocumentReview.user_id == user_id,
            ApplicationDocumentReview.application_id == application_id, ApplicationDocumentReview.package_id == package_id,
        ))
        if review is None:
            raise ReviewNotFoundError
        return await self._response(review)

    async def acknowledge(self, *, user_id: int, application_id: int, package_id: int, review_id: str, payload: ReviewAcknowledgmentRequest) -> DocumentReviewResponse:
        review = await self._review_owned(user_id, application_id, package_id, review_id)
        if review.package_content_sha256 != payload.package_content_sha256:
            raise ReviewConflictError("Review no longer matches this package")
        valid_ids = {str(item.get("id")) for item in review.findings}
        for item in payload.acknowledgments:
            if item.finding_id not in valid_ids:
                raise ReviewNotFoundError
            row = await self._session.scalar(select(ApplicationReviewAcknowledgment).where(
                ApplicationReviewAcknowledgment.review_id == review.id,
                ApplicationReviewAcknowledgment.finding_id == item.finding_id,
            ))
            if row is None:
                self._session.add(ApplicationReviewAcknowledgment(
                    review_id=review.id, package_id=review.package_id,
                    finding_id=item.finding_id, disposition=item.disposition,
                ))
            else:
                row.disposition = item.disposition
        await self._session.flush()
        return await self._response(review)

    async def current(self, *, user_id: int, package: ApplicationPackage | None) -> ApplicationDocumentReview | None:
        if package is None:
            return None
        return await self._cached(user_id=user_id, package=package)

    async def _cached(self, *, user_id: int, package: ApplicationPackage) -> ApplicationDocumentReview | None:
        latest = await self._latest_matching_review(user_id=user_id, package=package)
        return latest if latest is not None and latest.state == "completed" else None

    async def _latest_matching_review(
        self, *, user_id: int, package: ApplicationPackage
    ) -> ApplicationDocumentReview | None:
        return cast(ApplicationDocumentReview | None, await self._session.scalar(select(ApplicationDocumentReview).where(
            ApplicationDocumentReview.user_id == user_id,
            ApplicationDocumentReview.package_id == package.id,
            ApplicationDocumentReview.package_content_sha256 == package.content_sha256,
            ApplicationDocumentReview.review_policy_version == REVIEW_POLICY_VERSION,
        ).order_by(ApplicationDocumentReview.created_at.desc(), ApplicationDocumentReview.id.desc())))

    async def _active(
        self, *, user_id: int, application_id: int, package: ApplicationPackage
    ) -> ApplicationDocumentReview | None:
        return cast(ApplicationDocumentReview | None, await self._session.scalar(
            select(ApplicationDocumentReview).where(
                ApplicationDocumentReview.user_id == user_id,
                ApplicationDocumentReview.application_id == application_id,
                ApplicationDocumentReview.package_id == package.id,
                ApplicationDocumentReview.package_content_sha256 == package.content_sha256,
                ApplicationDocumentReview.review_policy_version == REVIEW_POLICY_VERSION,
                ApplicationDocumentReview.state.in_(("queued", "running")),
            )
        ))

    async def _owned_package(self, *, user_id: int, application_id: int, package_id: int) -> ApplicationPackage:
        package = await self._session.scalar(select(ApplicationPackage).join(Application).where(
            ApplicationPackage.id == package_id, ApplicationPackage.application_id == application_id,
            Application.user_id == user_id,
        ))
        if package is None:
            raise ReviewNotFoundError
        return package

    async def _review_owned(self, user_id: int, application_id: int, package_id: int, review_id: str) -> ApplicationDocumentReview:
        review = await self._session.scalar(select(ApplicationDocumentReview).where(
            ApplicationDocumentReview.id == review_id, ApplicationDocumentReview.user_id == user_id,
            ApplicationDocumentReview.application_id == application_id, ApplicationDocumentReview.package_id == package_id,
        ))
        if review is None:
            raise ReviewNotFoundError
        return review

    async def _validate_package(self, package: ApplicationPackage) -> None:
        encoded = json.dumps(package.content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(encoded.encode()).hexdigest() != package.content_sha256:
            raise ReviewConflictError("Package content is no longer internally consistent")
        if len(encoded) > MAX_REVIEW_INPUT_CHARS:
            raise ReviewConflictError("Package is too large to review; reduce selected material first")
        resume = package.content.get("resume") if isinstance(package.content, dict) else None
        if not isinstance(resume, dict) or not isinstance(resume.get("content"), dict) or not isinstance(resume.get("provenance"), dict):
            raise ReviewConflictError("Package lacks validated source-backed resume evidence")

    async def _response(self, review: ApplicationDocumentReview) -> DocumentReviewResponse:
        acknowledgments = list((await self._session.execute(select(ApplicationReviewAcknowledgment).where(
            ApplicationReviewAcknowledgment.review_id == review.id
        ))).scalars().all())
        return DocumentReviewResponse(
            id=review.id, application_id=review.application_id, package_id=review.package_id,
            package_content_sha256=review.package_content_sha256,
            review_policy_version=review.review_policy_version, state=review.state, outcome=review.outcome,
            findings=[DocumentReviewFinding.model_validate(item) for item in review.findings],
            failure_code=review.failure_code, created_at=review.created_at, completed_at=review.completed_at,
            acknowledgments=[ReviewAcknowledgmentItem(finding_id=row.finding_id, disposition=row.disposition) for row in acknowledgments],
        )

    async def _current_owned_package(self, *, user_id: int, application_id: int, package_id: int) -> ApplicationPackage:
        package = await self._owned_package(user_id=user_id, application_id=application_id, package_id=package_id)
        preparation = await self._session.get(ApplicationPreparation, application_id)
        projected = await ApplicationPreparationService(session=self._session).get(
            user_id=user_id, application_id=application_id
        )
        if (
            preparation is None
            or preparation.reviewable_package_id != package.id
            or projected.state not in {"needs_review", "ready"}
        ):
            raise ReviewConflictError("Package is no longer current for review")
        return package


async def run_document_review_background(*, task_id: str, review_id: str, user_id: int) -> None:
    """Run the bounded review in a fresh session; task results expose IDs only."""
    store = TaskStore(get_redis())
    diagnostic_emitted = False
    generation_summaries: list[dict[str, object]] = []
    try:
        async with async_session_factory() as session:
            review = await session.scalar(select(ApplicationDocumentReview).where(
                ApplicationDocumentReview.id == review_id, ApplicationDocumentReview.user_id == user_id,
            ).with_for_update())
            if review is None or review.state != "queued":
                await store.update(task_id, status="failed", message="Document review is unavailable")
                return
            review.state = "running"; review.started_at = datetime.now(UTC)
            await session.commit()
            await store.update(task_id, status="running", message="Reviewing documents", review_id=review_id)
            package = await session.get(ApplicationPackage, review.package_id)
            if package is None:
                raise ReviewConflictError("Package is unavailable")
            service = DocumentReviewService(session)
            await service._validate_package(package)
            evidence_catalog, provider_projection = _build_evidence_catalog_and_projection(package.content)
            try:
                async with asyncio.timeout(_REVIEW_TIMEOUT_SECONDS):
                    deadline = monotonic() + _REVIEW_TIMEOUT_SECONDS
                    corrective_metadata: dict[str, object] | None = None
                    for generation_attempt in (1, 2):
                        if generation_attempt == 2:
                            if corrective_metadata is None:
                                raise RuntimeError("missing corrective metadata")
                            remaining = deadline - monotonic()
                            if remaining <= 0:
                                raise TimeoutError
                            if "schema_issues" in corrective_metadata:
                                messages = schema_corrective_review_messages(
                                    projection=provider_projection,
                                    issues=cast(tuple[dict[str, str], ...], corrective_metadata["schema_issues"]),
                                )
                            else:
                                messages = corrective_review_messages(
                                    projection=provider_projection,
                                    reason=cast(str, corrective_metadata["reason"]),
                                    category=cast(str | None, corrective_metadata.get("category")),
                                    actual_documents=cast(tuple[str, ...] | None, corrective_metadata.get("actual_documents")),
                                )
                        else:
                            remaining = deadline - monotonic()
                            if remaining <= 0:
                                raise TimeoutError
                            messages = review_messages(projection=provider_projection)
                        try:
                            response, model = await llm_chat_completion(
                                messages=messages, json_mode=True, temperature=0.1,
                                timeout=min(60.0, remaining),
                            )
                        except APIStatusError as exc:
                            category = classify_provider_failure(exc)
                            if category not in {"auth_error", "other_http_error"}:
                                raise
                            _record_failure_diagnostic(
                                review=review, task_id=task_id, stage=_STAGE_PROVIDER_CALL, exc=exc,
                                provider_failure_category=category, generation_attempt=generation_attempt,
                                corrective_regeneration=generation_attempt == 2,
                            )
                            diagnostic_emitted = True
                            _append_generation_summary(generation_summaries, _generation_summary(
                                generation_attempt=generation_attempt, stage=_STAGE_PROVIDER_CALL,
                                provider_failure_category=category,
                            ))
                            await _fail(session, review, "unavailable", "Review unavailable", diagnostic_summary=_failure_diagnostic(
                                review, task_id, generation_summaries,
                            ))
                            await store.update(task_id, status="failed", message="Review unavailable", review_id=review_id)
                            return
                        structural_metadata = _response_structural_metadata(response)
                        try:
                            raw = response.choices[0].message.content or ""
                        except (AttributeError, IndexError, KeyError, TypeError) as exc:
                            _record_failure_diagnostic(
                                review=review, task_id=task_id, stage=_STAGE_PROVIDER_RESPONSE_EXTRACTION,
                                exc=exc, structural_metadata=structural_metadata, generation_attempt=generation_attempt,
                                corrective_regeneration=generation_attempt == 2,
                            )
                            diagnostic_emitted = True
                            _append_generation_summary(generation_summaries, _generation_summary(
                                generation_attempt=generation_attempt, stage=_STAGE_PROVIDER_RESPONSE_EXTRACTION,
                            ))
                            await _fail(session, review, "unavailable", "Review unavailable", diagnostic_summary=_failure_diagnostic(review, task_id, generation_summaries))
                            await store.update(task_id, status="failed", message="Review unavailable", review_id=review_id)
                            return
                        try:
                            parsed = json.loads(strip_code_fences(raw))
                        except (json.JSONDecodeError, TypeError) as exc:
                            _record_failure_diagnostic(
                                review=review, task_id=task_id, stage=_STAGE_RESPONSE_JSON_PARSE,
                                exc=exc, structural_metadata=structural_metadata, generation_attempt=generation_attempt,
                                corrective_regeneration=generation_attempt == 2,
                            )
                            diagnostic_emitted = True
                            _append_generation_summary(generation_summaries, _generation_summary(
                                generation_attempt=generation_attempt, stage=_STAGE_RESPONSE_JSON_PARSE,
                            ))
                            await _fail(session, review, "unavailable", "Review unavailable", diagnostic_summary=_failure_diagnostic(review, task_id, generation_summaries))
                            await store.update(task_id, status="failed", message="Review unavailable", review_id=review_id)
                            return
                        try:
                            provider_response = ProviderDocumentReviewResponseV10.model_validate(parsed)
                        except ValidationError as exc:
                            normalized_schema_issues = _normalize_schema_validation_issues(exc)
                            _log_schema_validation_failure(
                                generation=generation_attempt,
                                exc=exc,
                                normalized_schema_issues=normalized_schema_issues,
                            )
                            if generation_attempt == 1:
                                corrective_metadata = {
                                    "schema_issues": normalized_schema_issues,
                                }
                                _append_generation_summary(generation_summaries, _generation_summary(
                                    generation_attempt=generation_attempt,
                                    stage=_STAGE_RESPONSE_SCHEMA_VALIDATION,
                                ))
                                await _persist_corrective_diagnostic(session, review, task_id, generation_summaries)
                                continue
                            _record_failure_diagnostic(
                                review=review, task_id=task_id, stage=_STAGE_RESPONSE_SCHEMA_VALIDATION,
                                exc=exc, generation_attempt=generation_attempt,
                                corrective_regeneration=True,
                            )
                            diagnostic_emitted = True
                            _append_generation_summary(generation_summaries, _generation_summary(
                                generation_attempt=generation_attempt, stage=_STAGE_RESPONSE_SCHEMA_VALIDATION,
                            ))
                            await _fail(session, review, "unavailable", "Review unavailable", diagnostic_summary=_failure_diagnostic(review, task_id, generation_summaries))
                            await store.update(task_id, status="failed", message="Review unavailable", review_id=review_id)
                            return
                        try:
                            findings = _canonicalize_provider_review(provider_response, evidence_catalog)
                        except (ValidationError, _SchemaValidationError) as exc:
                            _log_canonical_validation_failure(generation=generation_attempt, exc=exc)
                            _record_failure_diagnostic(
                                review=review, task_id=task_id, stage=_STAGE_CANONICAL_VALIDATION,
                                exc=exc, generation_attempt=generation_attempt,
                                corrective_regeneration=generation_attempt == 2,
                            )
                            diagnostic_emitted = True
                            _append_generation_summary(generation_summaries, _generation_summary(
                                generation_attempt=generation_attempt, stage=_STAGE_CANONICAL_VALIDATION,
                            ))
                            await _fail(session, review, "unavailable", "Review unavailable", diagnostic_summary=_failure_diagnostic(review, task_id, generation_summaries))
                            await store.update(task_id, status="failed", message="Review unavailable", review_id=review_id)
                            return
                        except _EvidenceValidationError as exc:
                            if generation_attempt == 1 and _is_corrective_regeneration_eligible(exc):
                                metadata = exc.structural_metadata or {}
                                corrective_metadata = {
                                    "reason": exc.reason,
                                    "category": metadata.get("category"),
                                    "actual_documents": metadata.get("actual_documents"),
                                }
                                _record_failure_diagnostic(
                                    review=review, task_id=task_id, stage=_STAGE_CORRECTIVE_REGENERATION,
                                    exc=exc, evidence_reason=exc.reason,
                                    generation_attempt=generation_attempt, corrective_regeneration=True,
                                )
                                _append_generation_summary(generation_summaries, _generation_summary(
                                    generation_attempt=generation_attempt, stage=_STAGE_EVIDENCE_VALIDATION,
                                    reason=exc.reason,
                                ))
                                await _persist_corrective_diagnostic(session, review, task_id, generation_summaries)
                                continue
                            _record_failure_diagnostic(
                                review=review, task_id=task_id, stage=_STAGE_EVIDENCE_VALIDATION,
                                exc=exc, evidence_reason=exc.reason, structural_metadata=exc.structural_metadata,
                                generation_attempt=generation_attempt, corrective_regeneration=generation_attempt == 2,
                            )
                            diagnostic_emitted = True
                            _append_generation_summary(generation_summaries, _generation_summary(
                                generation_attempt=generation_attempt, stage=_STAGE_EVIDENCE_VALIDATION,
                                reason=exc.reason, structural_metadata=exc.structural_metadata,
                            ))
                            await _fail(session, review, "unavailable", "Review unavailable", diagnostic_summary=_failure_diagnostic(review, task_id, generation_summaries))
                            await store.update(task_id, status="failed", message="Review unavailable", review_id=review_id)
                            return
                        break
            except TimeoutError:
                await _fail(session, review, "timeout", "Review timed out", diagnostic_summary=_failure_diagnostic(
                    review, task_id, generation_summaries,
                ))
                await store.update(task_id, status="failed", message="Review unavailable", review_id=review_id)
                return
            except LLMUnavailableError as exc:
                _record_failure_diagnostic(
                    review=review,
                    task_id=task_id,
                    stage=_STAGE_PROVIDER_CALL,
                    exc=exc,
                    provider_failure_category=exc.provider_failure_category,
                    providers_exhausted=exc.providers_exhausted,
                    attempt_count=exc.attempt_count,
                    attempted_provider_names=exc.attempted_provider_names,
                    attempt_categories=exc.attempt_categories,
                    generation_attempt=generation_attempt,
                    corrective_regeneration=generation_attempt == 2,
                )
                diagnostic_emitted = True
                _append_generation_summary(generation_summaries, _generation_summary(
                    generation_attempt=generation_attempt, stage=_STAGE_PROVIDER_CALL,
                    provider_failure_category=exc.provider_failure_category,
                    provider_attempt_count=exc.attempt_count, providers_exhausted=exc.providers_exhausted,
                ))
                await _fail(session, review, "unavailable", "Review unavailable", diagnostic_summary=_failure_diagnostic(review, task_id, generation_summaries))
                await store.update(task_id, status="failed", message="Review unavailable", review_id=review_id)
                return
            try:
                await session.refresh(review, with_for_update=True)
                if review.state != "running":
                    return
                review.state = "completed"; review.outcome = "attention_needed" if findings else "no_findings"
                review.findings = findings; review.model_name = model; review.prompt_version = REVIEW_POLICY_VERSION; review.completed_at = datetime.now(UTC)
                review.diagnostic_summary = _success_diagnostic(review, task_id, generation_attempt)
                await _commit_with_diagnostic_fallback(session, review, terminal_state="completed")
            except Exception as exc:
                _record_failure_diagnostic(
                    review=review, task_id=task_id, stage=_STAGE_COMPLETION_PERSISTENCE, exc=exc,
                )
                diagnostic_emitted = True
                await _fail(session, review, "unavailable", "Review unavailable", diagnostic_summary=_failure_diagnostic(review, task_id, generation_summaries))
                await store.update(task_id, status="failed", message="Review unavailable", review_id=review_id)
                return
            try:
                await store.update(task_id, status="done", message="Document review completed", review_id=review_id)
            except Exception as exc:
                _record_failure_diagnostic(
                    review=review, task_id=task_id, stage=_STAGE_TASK_COMPLETION, exc=exc,
                )
                diagnostic_emitted = True
                return
    except Exception as exc:
        # Never include package or provider response in logs/task data.
        if not diagnostic_emitted:
            _record_failure_diagnostic(
                review_id=review_id, task_id=task_id, stage=_STAGE_WORKER_UNKNOWN, exc=exc,
            )
        async with async_session_factory() as session:
            review = await session.get(ApplicationDocumentReview, review_id)
            if review is not None and review.state in {"queued", "running"}:
                await _fail(session, review, "unavailable", "Review unavailable", diagnostic_summary=_failure_diagnostic(review, task_id, generation_summaries))
        await store.update(task_id, status="failed", message="Review unavailable", review_id=review_id)


async def _fail(
    session: AsyncSession, review: ApplicationDocumentReview, code: str, message: str,
    *, diagnostic_summary: dict[str, object] | None = None,
) -> None:
    if review.state not in {"queued", "running"}:
        return
    review.state = "failed"; review.outcome = "insufficient_evidence"; review.failure_code = code
    review.failure_message = message; review.completed_at = datetime.now(UTC)
    review.diagnostic_summary = diagnostic_summary
    await _commit_with_diagnostic_fallback(session, review, terminal_state="failed")


def _append_generation_summary(
    summaries: list[dict[str, object]], summary: dict[str, object],
) -> None:
    """Keep durable state aligned to the worker's fixed two-generation loop."""
    if len(summaries) < _MAX_DIAGNOSTIC_GENERATIONS:
        summaries.append(summary)


def _generation_summary(
    *, generation_attempt: int, stage: str, reason: str | None = None,
    structural_metadata: dict[str, object] | None = None,
    provider_failure_category: str | None = None, provider_attempt_count: int | None = None,
    providers_exhausted: bool | None = None,
) -> dict[str, object]:
    """Build the closed durable contract without copying logger metadata."""
    summary: dict[str, object] = {
        "generation_attempt": generation_attempt,
        "stage": stage,
    }
    if reason is not None:
        summary["reason"] = reason
    if structural_metadata is not None:
        for key in ("pointer_failure_kind", "document", "locator_role"):
            value = structural_metadata.get(key)
            if isinstance(value, str):
                summary[key] = value
    if provider_failure_category is not None:
        summary["provider_failure_category"] = provider_failure_category
    if provider_attempt_count is not None:
        summary["provider_attempt_count"] = provider_attempt_count
    if providers_exhausted is not None:
        summary["providers_exhausted"] = providers_exhausted
    return summary


def _failure_diagnostic(
    review: ApplicationDocumentReview, task_id: str, summaries: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "diagnostic_version": _DIAGNOSTIC_VERSION,
        "review_id": review.id,
        "task_id": task_id,
        "outcome": "failed",
        "generations": summaries[:_MAX_DIAGNOSTIC_GENERATIONS],
    }


def _success_diagnostic(
    review: ApplicationDocumentReview, task_id: str, generation_attempt: int,
) -> dict[str, object]:
    return {
        "diagnostic_version": _DIAGNOSTIC_VERSION,
        "review_id": review.id,
        "task_id": task_id,
        "outcome": "completed",
        "generation_count": generation_attempt,
        "completed_on_generation": generation_attempt,
    }


async def _persist_corrective_diagnostic(
    session: AsyncSession, review: ApplicationDocumentReview, task_id: str,
    summaries: list[dict[str, object]],
) -> None:
    """Checkpoint only the first eligible correction before generation two."""
    review.diagnostic_summary = {
        "diagnostic_version": _DIAGNOSTIC_VERSION,
        "review_id": review.id,
        "task_id": task_id,
        "outcome": "running",
        "generations": summaries[:_MAX_DIAGNOSTIC_GENERATIONS],
    }
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        logger.warning("document_review.diagnostic_persistence_failed")
        await session.refresh(review, with_for_update=True)
        review.diagnostic_summary = None


async def _commit_with_diagnostic_fallback(
    session: AsyncSession, review: ApplicationDocumentReview, *, terminal_state: Literal["completed", "failed"],
) -> None:
    """Keep an internal diagnostic-write problem from changing a review result."""
    if terminal_state == "completed":
        completed_values = (
            review.outcome, review.findings, review.model_name, review.prompt_version, review.completed_at,
        )
        failed_values: tuple[str | None, str | None, datetime | None] | None = None
    else:
        completed_values = None
        failed_values = (review.failure_code, review.failure_message, review.completed_at)
    try:
        await session.commit()
        return
    except Exception:
        await session.rollback()
        logger.warning("document_review.diagnostic_persistence_failed")

    if terminal_state == "completed":
        assert completed_values is not None
        outcome, findings, model_name, prompt_version, completed_at = completed_values
        await session.refresh(review, with_for_update=True)
        review.state = "completed"; review.outcome = outcome; review.findings = findings
        review.model_name = model_name; review.prompt_version = prompt_version; review.completed_at = completed_at
    else:
        assert failed_values is not None
        failure_code, failure_message, completed_at = failed_values
        await session.refresh(review, with_for_update=True)
        review.state = "failed"; review.outcome = "insufficient_evidence"
        review.failure_code = failure_code; review.failure_message = failure_message; review.completed_at = completed_at
    review.diagnostic_summary = None
    await session.commit()


def _record_failure_diagnostic(
    *, review: ApplicationDocumentReview | None = None, review_id: str | None = None,
    task_id: str, stage: str, exc: BaseException, evidence_reason: str | None = None,
    structural_metadata: dict[str, object] | None = None,
    provider_failure_category: str | None = None, providers_exhausted: bool | None = None,
    attempt_count: int | None = None, attempted_provider_names: tuple[str, ...] | None = None,
    attempt_categories: tuple[str, ...] | None = None,
    generation_attempt: int | None = None, corrective_regeneration: bool | None = None,
) -> None:
    """Log only fixed diagnostic fields; never render exception or provider content."""
    fields: dict[str, object] = {
        "review_id": review.id if review is not None else review_id,
        "task_id": task_id,
        "stage": stage,
        "exception_class": type(exc).__name__,
    }
    if review is not None:
        fields["package_id"] = review.package_id
    if evidence_reason is not None:
        fields["evidence_reason"] = evidence_reason
    if isinstance(exc, ValidationError):
        fields["validation_error_count"] = exc.error_count()
    if structural_metadata is not None:
        fields.update(structural_metadata)
    if provider_failure_category is not None:
        fields["provider_failure_category"] = provider_failure_category
    if providers_exhausted is not None:
        fields["providers_exhausted"] = providers_exhausted
    if attempt_count is not None:
        fields["attempt_count"] = attempt_count
    if attempted_provider_names is not None:
        fields["attempted_provider_names"] = attempted_provider_names
    if attempt_categories is not None:
        fields["attempt_categories"] = attempt_categories
    if generation_attempt is not None:
        fields["generation_attempt"] = generation_attempt
    if corrective_regeneration is not None:
        fields["corrective_regeneration"] = corrective_regeneration
    logger.warning("document_review.failed", **fields)


def _response_structural_metadata(response: object) -> dict[str, object]:
    """Return allowlisted, non-content metadata for response compatibility failures."""
    metadata: dict[str, object] = {"response_type": type(response).__name__}
    try:
        response_value = cast(Any, response)
        choices = response_value.choices
        if not isinstance(choices, (list, tuple)):
            return metadata
        metadata["choices_count"] = len(choices)
        if not choices:
            return metadata
        message = choices[0].message
        metadata["message_type"] = type(message).__name__
        content = message.content
        metadata["content_type"] = type(content).__name__
        if isinstance(content, str):
            metadata["content_length"] = len(content)
        elif isinstance(content, list):
            metadata["content_block_count"] = len(content)
    except Exception:
        return metadata
    return metadata


def _escape_json_pointer_segment(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def _build_evidence_catalog_and_projection(
    package: dict[str, object],
) -> tuple[dict[str, _EvidenceCatalogEntry], dict[str, object]]:
    """Build the ephemeral catalog and provider projection in one tree walk."""
    catalog: dict[str, _EvidenceCatalogEntry] = {}
    next_id = {document: 1 for document in _EVIDENCE_ROOTS}

    def walk(value: object, *, document: Literal["job", "resume", "cover_letter"], locator: str) -> object:
        if isinstance(value, str):
            if not value:
                return value
            evidence_id = f"{_PROVIDER_EVIDENCE_ID_PREFIXES[document]}-e{next_id[document]:04d}"
            next_id[document] += 1
            catalog[evidence_id] = _EvidenceCatalogEntry(
                evidence_id=evidence_id, document=document, locator=locator, text=value,
            )
            return {"evidence_id": evidence_id, "text": value}
        if isinstance(value, list):
            return [walk(item, document=document, locator=f"{locator}/{index}") for index, item in enumerate(value)]
        if isinstance(value, dict):
            projection: dict[str, object] = {}
            for key in sorted(value):
                if not isinstance(key, str):
                    raise ValueError("review package contains a non-string object key")
                projection[key] = walk(
                    value[key], document=document,
                    locator=f"{locator}/{_escape_json_pointer_segment(key)}",
                )
            return projection
        return value

    projection: dict[str, object] = {}
    for root in _EVIDENCE_ROOTS:
        value = package.get(root)
        if value is not None:
            projection[root] = walk(value, document=cast(Literal["job", "resume", "cover_letter"], root), locator=f"/{root}")
    return catalog, projection


def _canonicalize_provider_review(
    provider_response: ProviderDocumentReviewResponseV10, catalog: dict[str, _EvidenceCatalogEntry],
) -> list[dict[str, object]]:
    """Resolve an already-validated v8 provider response into canonical models."""
    findings: list[dict[str, object]] = []
    finding_ids: set[str] = set()
    for provider_finding in provider_response.findings:
        if provider_finding.id in finding_ids:
            raise _SchemaValidationError("duplicate_finding_id")
        finding_ids.add(provider_finding.id)
        findings.append(_canonicalize_provider_finding(provider_finding, catalog).model_dump())
    return findings


def _canonicalize_provider_finding(
    finding: ProviderFindingV10, catalog: dict[str, _EvidenceCatalogEntry],
) -> DocumentReviewFinding:
    contract = REVIEW_CATEGORY_CONTRACTS[finding.category]
    selections = _provider_evidence_selections(finding)
    if len({evidence_id for evidence_id, _documents in selections}) != len(selections):
        raise _EvidenceValidationError("duplicate_evidence_id")
    resolved: list[_EvidenceCatalogEntry] = []
    for evidence_id, allowed_documents in selections:
        entry = catalog.get(evidence_id)
        if entry is None:
            raise _EvidenceValidationError("invalid_evidence_id")
        if entry.document not in allowed_documents:
            raise _EvidenceValidationError("invalid_evidence_document")
        resolved.append(entry)
    if len(resolved) < contract.expected_evidence_count:
        raise _EvidenceValidationError(
            "missing_required_evidence",
            structural_metadata={
                "category": finding.category,
                "required_documents": tuple(sorted(contract.roles_by_document)),
                "actual_documents": tuple(sorted(entry.document for entry in resolved)),
            },
        )
    actual_documents = tuple(sorted(entry.document for entry in resolved))
    for entry in resolved:
        if entry.document not in contract.roles_by_document:
            raise _EvidenceValidationError(
                "invalid_evidence_document",
                structural_metadata={
                    "category": finding.category,
                    "required_documents": tuple(sorted(contract.roles_by_document)),
                    "actual_documents": actual_documents,
                },
            )
    derived = sorted(
        ((contract.roles_by_document[entry.document], entry) for entry in resolved),
        key=lambda item: item[0],
    )
    roles = [role for role, _entry in derived]
    if len(set(roles)) != len(roles):
        raise _EvidenceValidationError(
            "duplicate_evidence_role",
            structural_metadata={
                "category": finding.category,
                "required_documents": tuple(sorted(contract.roles_by_document)),
                "actual_documents": actual_documents,
            },
        )
    if not contract.required_roles.issubset(roles):
        raise _EvidenceValidationError(
            "missing_required_evidence",
            structural_metadata={
                "category": finding.category,
                "required_documents": tuple(sorted(contract.roles_by_document)),
                "actual_documents": actual_documents,
            },
        )
    target_entries = [entry for role, entry in derived if role == "target"]
    if len(target_entries) != 1:
        raise _EvidenceValidationError("missing_required_evidence")
    target = target_entries[0]
    return DocumentReviewFinding(
        id=finding.id, document=target.document, category=finding.category,
        severity=finding.severity, excerpt=target.text, locator=target.locator,
        rationale=finding.rationale,
        evidence=[
            {"role": role, "document": entry.document, "locator": entry.locator, "excerpt": entry.text}
            for role, entry in derived
        ],
        proposed_action=finding.proposed_action,
    )


def _provider_evidence_selections(
    finding: ProviderFindingV10,
) -> tuple[tuple[str, frozenset[Literal["job", "resume", "cover_letter"]]], ...]:
    """Extract validated provider slots without granting them canonical roles."""
    if isinstance(finding, ProviderSingleTargetFindingV10):
        return ((finding.target_evidence_id, frozenset({"resume", "cover_letter"})),)
    if isinstance(finding, ProviderCrossDocumentFindingV10):
        return (
            (finding.resume_evidence_id, frozenset({"resume"})),
            (finding.cover_letter_evidence_id, frozenset({"cover_letter"})),
        )
    if isinstance(finding, ProviderJobTargetFindingV10):
        return (
            (finding.job_evidence_id, frozenset({"job"})),
            (finding.target_evidence_id, frozenset({"resume", "cover_letter"})),
        )
    raise _SchemaValidationError("unsupported_provider_finding_shape")




def _validate_findings(raw: str, package: dict[str, object]) -> list[dict[str, object]]:
    parsed = json.loads(strip_code_fences(raw))
    return _validate_structured_findings(parsed, package)


def _validate_structured_findings(parsed: object, package: dict[str, object]) -> list[dict[str, object]]:
    entries = parsed.get("findings") if isinstance(parsed, dict) else None
    if not isinstance(entries, list) or len(entries) > MAX_FINDINGS:
        raise _SchemaValidationError("invalid_finding_shape")
    validated: list[dict[str, object]] = []
    for entry in entries:
        finding = DocumentReviewFinding.model_validate(entry)
        if finding.id in {item["id"] for item in validated}:
            raise _SchemaValidationError("duplicate_finding_id")
        _validate_finding_evidence(finding, package)
        validated.append(finding.model_dump())
    return validated


def _validate_finding_evidence(finding: DocumentReviewFinding, package: dict[str, object]) -> None:
    if not finding.locator.startswith(f"/{finding.document}"):
        raise _EvidenceValidationError("invalid_document")
    _require_exact_text_evidence(
        _resolve_json_pointer(package, finding.locator),
        finding.excerpt,
        document=finding.document,
        locator_role="finding",
    )
    if not finding.evidence:
        raise _EvidenceValidationError("missing_required_evidence")
    seen: set[tuple[str, str]] = set()
    for evidence in finding.evidence:
        identity = (evidence.role, evidence.document)
        if identity in seen:
            raise _EvidenceValidationError("duplicate_evidence_role")
        if not evidence.locator.startswith(f"/{evidence.document}"):
            raise _EvidenceValidationError("invalid_document")
        _require_exact_text_evidence(
            _resolve_json_pointer(package, evidence.locator),
            evidence.excerpt,
            document=evidence.document,
            locator_role="evidence",
        )
        seen.add(identity)
    if seen != _required_evidence_roles(finding.category, finding.document):
        raise _EvidenceValidationError("required_role_mismatch")


def _require_exact_text_evidence(
    resolution: _JsonPointerResolution,
    excerpt: str,
    *,
    document: str,
    locator_role: _LocatorRole,
) -> None:
    if resolution.failure_kind is not None:
        raise _EvidenceValidationError(
            "invalid_pointer",
            structural_metadata={
                "pointer_failure_kind": resolution.failure_kind,
                "document": document,
                "locator_role": locator_role,
                "pointer_depth": resolution.pointer_depth,
                "failing_segment_position": resolution.failing_segment_position,
                "container_kind": resolution.container_kind,
            },
        )
    if resolution.value is None:
        # Defensive compatibility guard; normal null resolution is classified above.
        raise _EvidenceValidationError("invalid_pointer")
    if not isinstance(resolution.value, str):
        raise _EvidenceValidationError("pointer_not_string_leaf")
    if resolution.value != excerpt:
        raise _EvidenceValidationError("excerpt_mismatch")


def _is_exact_text_evidence(value: object | None, excerpt: str) -> bool:
    """Evidence locators name one immutable textual leaf, not a container."""
    return isinstance(value, str) and value == excerpt


def _required_evidence_roles(category: str, document: str) -> set[tuple[str, str]]:
    contract = REVIEW_CATEGORY_CONTRACTS.get(category)
    if contract is None:
        return {("target", document)}
    documents_by_role: dict[str, list[str]] = {}
    for evidence_document, role in contract.roles_by_document.items():
        documents_by_role.setdefault(role, []).append(evidence_document)
    return {
        (role, documents[0] if len(documents) == 1 else document)
        for role, documents in documents_by_role.items()
    }


def _container_kind(value: object) -> _PointerContainerKind:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    return "scalar"


def _resolve_json_pointer(value: object, pointer: str) -> _JsonPointerResolution:
    """Resolve using the existing strict semantics and retain safe failure topology.

    No leading slash fails before traversal with depth zero. Object keys must
    exist exactly. Array segments must be digit-only and their parsed index
    must be below the list length (so negative indices remain invalid). Any
    scalar or null encountered before the final segment is a type mismatch.
    A terminal JSON null remains an unsuccessful resolution.
    """
    if not pointer.startswith("/"):
        return _JsonPointerResolution(None, "missing_leading_slash", 0, None, None)
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    current: object = value
    for position, part in enumerate(parts):
        if isinstance(current, dict):
            if part not in current:
                return _JsonPointerResolution(None, "missing_key", len(parts), position, "object")
            current = current[part]
        elif isinstance(current, list):
            if not part.isdigit():
                return _JsonPointerResolution(None, "invalid_array_index", len(parts), position, "array")
            try:
                index = int(part)
            except ValueError:
                return _JsonPointerResolution(None, "unknown_pointer_failure", len(parts), position, "array")
            if index >= len(current):
                return _JsonPointerResolution(None, "array_index_out_of_range", len(parts), position, "array")
            current = current[index]
        else:
            return _JsonPointerResolution(
                None, "traversal_type_mismatch", len(parts), position, _container_kind(current),
            )
    if current is None:
        return _JsonPointerResolution(None, "resolved_null", len(parts), None, "null")
    return _JsonPointerResolution(current, None, len(parts), None, _container_kind(current))


def _json_pointer(value: object, pointer: str) -> object | None:
    """Compatibility wrapper preserving the existing pointer-value contract."""
    return _resolve_json_pointer(value, pointer).value


def _evidence_ids(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"source_id", "evidence_id"} and isinstance(child, str):
                found.add(child)
            found.update(_evidence_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_evidence_ids(child))
    return found
