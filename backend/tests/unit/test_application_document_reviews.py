# ruff: noqa: E501
"""Pure reviewer-output safety checks; provider calls are always faked elsewhere."""

import asyncio
import hashlib
import json
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from openai import APIStatusError, AuthenticationError, BadRequestError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pytest import MonkeyPatch

from app.domain.applications import document_review
from app.domain.applications.document_review import (
    _json_pointer,
    _resolve_json_pointer,
    _validate_findings,
)
from app.domain.applications.review_prompts import (
    MAX_REVIEW_INPUT_CHARS,
    REVIEW_POLICY_VERSION,
    corrective_review_messages,
    review_messages,
    schema_corrective_review_messages,
)
from app.domain.applications.schemas import (
    DocumentReviewResponse,
    ProviderDocumentReviewResponseV10,
)
from app.infrastructure.llm_client import LLMUnavailableError
from app.infrastructure.task_store import TaskStore
from app.models.application_package import ApplicationPackage
from app.models.application_review import ApplicationDocumentReview

pytestmark = pytest.mark.unit


def _provider_status_error(
    status: int,
    cls: type[APIStatusError] = APIStatusError,
) -> APIStatusError:
    return cls(
        "SECRET_PROVIDER_BODY_SENTINEL",
        response=httpx.Response(
            status,
            request=httpx.Request("POST", "https://provider.invalid/v1/chat/completions"),
        ),
        body={"detail": "SECRET_AUTH_DETAIL_SENTINEL"},
    )


def _package() -> dict[str, object]:
    return {
        "job": {"description": "Requires Python"},
        "resume": {
            "content": {"summary": "Built APIs", "nested": ["Leaf evidence"]},
            "provenance": {"source_revision_sha256": "a"},
        },
        "cover_letter": {"content": "Built APIs for this role"},
    }


def _current_shape_package() -> dict[str, object]:
    return {
        "job": {"title": "Engineer", "company": "Example Co", "description": "Requires Python"},
        "resume": {
            "content": {
                "matched_skills": ["Python"],
                "canonical_sections": [{"items": [{"text": "Built API services"}]}],
                "work_history": [
                    {
                        "title": "Backend Engineer",
                        "tailored_highlights": ["Automated deployment checks"],
                    }
                ],
            },
            "provenance": {"source_revision_sha256": "a"},
        },
        "cover_letter": {"mode": "string"},
    }


def _finding(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "f1",
        "document": "resume",
        "category": "repetition",
        "severity": "suggestion",
        "excerpt": "Built APIs",
        "locator": "/resume/content/summary",
        "rationale": "Repeated wording",
        "evidence": [
            {
                "role": "target",
                "document": "resume",
                "locator": "/resume/content/summary",
                "excerpt": "Built APIs",
            }
        ],
        "proposed_action": "Vary the wording",
    }
    value.update(overrides)
    return value


def _legacy_test_review_prompt_explicitly_describes_the_response_contract() -> None:
    prompt = review_messages(projection=_package())[0]["content"]

    for required_text in (
        '"findings"',
        "id",
        "document",
        "category",
        "severity",
        "excerpt",
        "locator",
        "rationale",
        "evidence",
        "proposed_action",
        "resume",
        "cover_letter",
        "concern",
        "suggestion",
        "unsupported_claim",
        "cross_document_mismatch",
        "job_document_mismatch",
        "omitted_verified_evidence",
        "repetition",
        "clarity",
        "keyword_stuffing",
        "target",
        "comparison",
        "requirement",
        "job",
        "exact string leaf",
    ):
        assert required_text in prompt


def _legacy_test_review_prompt_teaches_current_absolute_leaf_pointer_contract() -> None:
    prompt = review_messages(projection=_current_shape_package())[0]["content"]

    for required_text in (
        'document="job" → locator must begin with /job',
        'document="resume" → locator must begin with /resume',
        'document="cover_letter" → locator must begin with /cover_letter',
        "/resume/content/matched_skills/0",
        "/resume/content/canonical_sections/0/items/0/text",
        "/resume/content/work_history/0/title",
        "/resume/content/work_history/0/tailored_highlights/0",
        "ONE string leaf",
        "not stop on an object, array/list",
        "excerpt must equal the ENTIRE resolved string",
        "excerpt must exactly equal the full resolved string leaf",
        "do not fabricate a substantive cover-letter finding",
    ):
        assert required_text in prompt
    assert "/resume/content/summary" not in prompt


def _legacy_test_review_prompt_requires_existing_paths_for_each_locator() -> None:
    prompt = review_messages(projection=_current_shape_package())[0]["content"]

    for required_text in (
        "These are examples only",
        "do not copy or extrapolate them unless that exact path exists in the supplied package",
        "does not imply index 1 or any other index exists",
        "For every finding.locator and every evidence[*].locator",
        "first inspect the actual supplied package JSON",
        "Every object segment in the JSON Pointer must be an existing key",
        "never invent a property name",
        "Every array segment must be a non-negative numeric index that exists in that exact array",
        "never emit nonnumeric, negative, out-of-range, invented, inferred, or extrapolated indices",
        "Inspect the supplied package before selecting EACH locator individually",
        "The selected pointer must resolve successfully in the supplied package",
    ):
        assert required_text in prompt


def test_prompt_representative_paths_resolve_to_string_leaves() -> None:
    package = _current_shape_package()
    for pointer in (
        "/job/description",
        "/resume/content/matched_skills/0",
        "/resume/content/canonical_sections/0/items/0/text",
        "/resume/content/work_history/0/title",
        "/resume/content/work_history/0/tailored_highlights/0",
    ):
        assert isinstance(_json_pointer(package, pointer), str)


@pytest.mark.parametrize(
    ("pointer", "failure_kind", "depth", "position", "container_kind"),
    [
        ("resume/content/summary", "missing_leading_slash", 0, None, None),
        ("/resume/content/missing", "missing_key", 3, 2, "object"),
        ("/resume/content/nested/not-an-index", "invalid_array_index", 4, 3, "array"),
        ("/resume/content/nested/-1", "invalid_array_index", 4, 3, "array"),
        ("/resume/content/nested/9", "array_index_out_of_range", 4, 3, "array"),
        ("/resume/content/summary/extra", "traversal_type_mismatch", 4, 3, "scalar"),
    ],
)
def test_json_pointer_failure_diagnostics_are_content_free_and_zero_based(
    pointer: str,
    failure_kind: str,
    depth: int,
    position: int | None,
    container_kind: str | None,
) -> None:
    resolution = _resolve_json_pointer(_package(), pointer)

    assert resolution.value is None
    assert resolution.failure_kind == failure_kind
    assert resolution.pointer_depth == depth
    assert resolution.failing_segment_position == position
    assert resolution.container_kind == container_kind


def test_json_pointer_terminal_null_has_a_structural_failure_kind() -> None:
    resolution = _resolve_json_pointer({"resume": {"content": None}}, "/resume/content")

    assert resolution.value is None
    assert resolution.failure_kind == "resolved_null"
    assert resolution.pointer_depth == 2
    assert resolution.failing_segment_position is None
    assert resolution.container_kind == "null"


def test_json_pointer_compatibility_wrapper_preserves_non_string_terminal_values() -> None:
    assert _json_pointer(_package(), "/resume/content/nested") == ["Leaf evidence"]


def test_validates_evidence_bound_finding() -> None:
    assert _validate_findings(json.dumps({"findings": [_finding()]}), _package())[0]["id"] == "f1"


@pytest.mark.parametrize(
    "field,value", [("locator", "/resume/content/missing"), ("excerpt", "Invented metric")]
)
def test_rejects_invalid_locator_or_excerpt(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        _validate_findings(json.dumps({"findings": [_finding(**{field: value})]}), _package())


def test_rejects_malicious_or_oversized_output() -> None:
    with pytest.raises(ValueError):
        _validate_findings('{"ignore_previous": true}', _package())
    with pytest.raises(ValueError):
        _validate_findings(
            json.dumps({"findings": [_finding(id=str(index)) for index in range(21)]}), _package()
        )


@pytest.mark.parametrize(
    "evidence",
    [
        [],
        [
            {
                "role": "target",
                "document": "resume",
                "locator": "/resume/content/missing",
                "excerpt": "Built APIs",
            }
        ],
        [
            {
                "role": "target",
                "document": "cover_letter",
                "locator": "/resume/content/summary",
                "excerpt": "Built APIs",
            }
        ],
        [
            {
                "role": "target",
                "document": "resume",
                "locator": "/resume/content/summary",
                "excerpt": "Invented",
            }
        ],
        [
            {
                "role": "target",
                "document": "resume",
                "locator": "/resume/content/summary",
                "excerpt": "Built APIs",
            },
            {
                "role": "target",
                "document": "resume",
                "locator": "/resume/content/summary",
                "excerpt": "Built APIs",
            },
        ],
    ],
)
def test_rejects_invalid_structured_evidence(evidence: list[dict[str, str]]) -> None:
    with pytest.raises(ValueError):
        _validate_findings(json.dumps({"findings": [_finding(evidence=evidence)]}), _package())


def test_requires_both_sides_for_comparison_categories() -> None:
    evidence = [
        {
            "role": "target",
            "document": "resume",
            "locator": "/resume/content/summary",
            "excerpt": "Built APIs",
        }
    ]
    with pytest.raises(ValueError):
        _validate_findings(
            json.dumps(
                {"findings": [_finding(category="cross_document_mismatch", evidence=evidence)]}
            ),
            _package(),
        )
    with pytest.raises(ValueError):
        _validate_findings(
            json.dumps(
                {"findings": [_finding(category="job_document_mismatch", evidence=evidence)]}
            ),
            _package(),
        )


def test_rejects_broad_object_list_and_partial_evidence_locators() -> None:
    broad_object = _finding(locator="/resume/content", excerpt="Built APIs")
    broad_list = _finding(locator="/resume/content/nested", excerpt="Leaf evidence")
    partial = _finding(
        excerpt="Built",
        evidence=[
            {
                "role": "target",
                "document": "resume",
                "locator": "/resume/content/summary",
                "excerpt": "Built",
            }
        ],
    )
    for finding in (broad_object, broad_list, partial):
        with pytest.raises(ValueError):
            _validate_findings(json.dumps({"findings": [finding]}), _package())
    leaf = _finding(
        locator="/resume/content/nested/0",
        excerpt="Leaf evidence",
        evidence=[
            {
                "role": "target",
                "document": "resume",
                "locator": "/resume/content/nested/0",
                "excerpt": "Leaf evidence",
            }
        ],
    )
    assert _validate_findings(json.dumps({"findings": [leaf]}), _package())[0]["id"] == "f1"


def test_rejects_finding_substring_when_its_evidence_is_exact() -> None:
    finding = _finding(
        excerpt="Built",
        evidence=[
            {
                "role": "target",
                "document": "resume",
                "locator": "/resume/content/summary",
                "excerpt": "Built APIs",
            }
        ],
    )
    with pytest.raises(ValueError):
        _validate_findings(json.dumps({"findings": [finding]}), _package())


@pytest.mark.parametrize(
    ("category", "document", "evidence"),
    [
        (
            "cross_document_mismatch",
            "resume",
            [
                {
                    "role": "target",
                    "document": "resume",
                    "locator": "/resume/content/summary",
                    "excerpt": "Built APIs",
                },
                {
                    "role": "comparison",
                    "document": "cover_letter",
                    "locator": "/cover_letter/content",
                    "excerpt": "Built APIs for this role",
                },
            ],
        ),
        (
            "job_document_mismatch",
            "resume",
            [
                {
                    "role": "requirement",
                    "document": "job",
                    "locator": "/job/description",
                    "excerpt": "Requires Python",
                },
                {
                    "role": "target",
                    "document": "resume",
                    "locator": "/resume/content/summary",
                    "excerpt": "Built APIs",
                },
            ],
        ),
        (
            "omitted_verified_evidence",
            "cover_letter",
            [
                {
                    "role": "requirement",
                    "document": "job",
                    "locator": "/job/description",
                    "excerpt": "Requires Python",
                },
                {
                    "role": "target",
                    "document": "cover_letter",
                    "locator": "/cover_letter/content",
                    "excerpt": "Built APIs for this role",
                },
            ],
        ),
    ],
)
def test_accepts_required_category_evidence_roles(
    category: str, document: str, evidence: list[dict[str, str]]
) -> None:
    target = (
        {"locator": "/cover_letter/content", "excerpt": "Built APIs for this role"}
        if document == "cover_letter"
        else {}
    )
    assert (
        _validate_findings(
            json.dumps(
                {
                    "findings": [
                        _finding(
                            category=category,
                            document=document,
                            evidence=evidence,
                            **target,
                        )
                    ]
                }
            ),
            _package(),
        )[0]["id"]
        == "f1"
    )


@pytest.mark.parametrize(
    ("category", "document", "evidence"),
    [
        (
            "cross_document_mismatch",
            "resume",
            [
                {
                    "role": "comparison",
                    "document": "resume",
                    "locator": "/resume/content/summary",
                    "excerpt": "Built APIs",
                },
                {
                    "role": "target",
                    "document": "cover_letter",
                    "locator": "/cover_letter/content",
                    "excerpt": "Built APIs for this role",
                },
            ],
        ),
        (
            "job_document_mismatch",
            "resume",
            [
                {
                    "role": "target",
                    "document": "job",
                    "locator": "/job/description",
                    "excerpt": "Requires Python",
                },
                {
                    "role": "requirement",
                    "document": "resume",
                    "locator": "/resume/content/summary",
                    "excerpt": "Built APIs",
                },
            ],
        ),
    ],
)
def test_rejects_swapped_evidence_roles(
    category: str, document: str, evidence: list[dict[str, str]]
) -> None:
    with pytest.raises(ValueError):
        _validate_findings(
            json.dumps(
                {
                    "findings": [
                        _finding(
                            category=category,
                            document=document,
                            evidence=evidence,
                        )
                    ]
                }
            ),
            _package(),
        )


class _MemoryTaskStore(TaskStore):
    def __init__(self, *, fail_done_update: bool = False) -> None:
        self.data: dict[str, dict[str, Any]] = {}
        self.fail_done_update = fail_done_update

    async def create(self, task_type: str, user_id: int) -> str:
        task_id = f"task-{len(self.data) + 1}"
        self.data[task_id] = {"type": task_type, "user_id": user_id, "status": "queued"}
        return task_id

    async def update(self, task_id: str, **fields: Any) -> None:
        if self.fail_done_update and fields.get("status") == "done":
            raise RuntimeError("task store failure")
        self.data.setdefault(task_id, {}).update(fields)


class _WorkerSession:
    def __init__(
        self,
        review: ApplicationDocumentReview,
        package: ApplicationPackage,
        *,
        fail_completion_commit: bool = False,
    ) -> None:
        self.review = review
        self.package = package
        self.fail_completion_commit = fail_completion_commit
        self.commit_count = 0
        self.rollback_count = 0

    async def scalar(self, _statement: object) -> ApplicationDocumentReview:
        return self.review

    async def get(self, model: type[object], _identity: object) -> object | None:
        if model is ApplicationPackage:
            return self.package
        if model is ApplicationDocumentReview:
            return self.review
        return None

    async def commit(self) -> None:
        self.commit_count += 1
        if self.fail_completion_commit and self.commit_count == 2:
            raise RuntimeError("persistence failure")
        return None

    async def rollback(self) -> None:
        self.rollback_count += 1
        return None

    async def refresh(self, _instance: object, **_kwargs: object) -> None:
        return None


class _SessionScope:
    def __init__(self, session: _WorkerSession) -> None:
        self.session = session

    async def __aenter__(self) -> _WorkerSession:
        return self.session

    async def __aexit__(self, _type: object, _value: object, _traceback: object) -> None:
        return None


def _worker_package() -> ApplicationPackage:
    content = _package()
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return ApplicationPackage(
        id=2,
        application_id=1,
        revision=1,
        content=content,
        content_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
        schema_version="application-package-v1",
        idempotency_key="package",
    )


def _current_shape_worker_package() -> ApplicationPackage:
    content = _current_shape_package()
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return ApplicationPackage(
        id=2,
        application_id=1,
        revision=1,
        content=content,
        content_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
        schema_version="application-package-v1",
        idempotency_key="package",
    )


class _DiagnosticLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))


def _review(
    package: ApplicationPackage, *, review_id: str = "review-worker"
) -> ApplicationDocumentReview:
    return ApplicationDocumentReview(
        id=review_id,
        user_id=1,
        application_id=1,
        package_id=package.id,
        package_content_sha256=package.content_sha256,
        review_policy_version=REVIEW_POLICY_VERSION,
        idempotency_key="review",
        task_id=f"task-{review_id}",
        state="queued",
        findings=[],
    )


def _install_worker(
    monkeypatch: MonkeyPatch,
    *,
    review: ApplicationDocumentReview,
    package: ApplicationPackage,
    provider: object,
    store: _MemoryTaskStore,
    session: _WorkerSession | None = None,
) -> _DiagnosticLogger:
    diagnostic_logger = _DiagnosticLogger()
    monkeypatch.setattr(
        document_review,
        "async_session_factory",
        lambda: _SessionScope(session or _WorkerSession(review, package)),
    )
    monkeypatch.setattr(document_review, "TaskStore", lambda _redis: store)
    monkeypatch.setattr(document_review, "get_redis", lambda: object())
    monkeypatch.setattr(document_review, "llm_chat_completion", provider)
    monkeypatch.setattr(document_review, "logger", diagnostic_logger)
    return diagnostic_logger


async def _run_worker(review: ApplicationDocumentReview) -> None:
    await document_review.run_document_review_background(
        task_id=review.task_id, review_id=review.id, user_id=1
    )


def _event_fields(logger: _DiagnosticLogger) -> dict[str, object]:
    assert len(logger.events) == 1
    assert logger.events[0][0] == "document_review.failed"
    return logger.events[0][1]


@pytest.mark.asyncio
async def test_background_timeout_is_terminal_and_late_provider_completion_is_ignored(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = ApplicationDocumentReview(
        id="review-timeout",
        user_id=1,
        application_id=1,
        package_id=package.id,
        package_content_sha256=package.content_sha256,
        review_policy_version=REVIEW_POLICY_VERSION,
        idempotency_key="review",
        task_id="task-timeout",
        state="queued",
        findings=[],
    )
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    late_release = asyncio.Event()
    late_finished = asyncio.Event()

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        async def complete_late() -> Any:
            await late_release.wait()
            late_finished.set()
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"findings": []}'))]
            )

        completion = asyncio.create_task(complete_late())
        return await asyncio.shield(completion), "fake-model"

    session = _WorkerSession(review, package)
    monkeypatch.setattr(document_review, "_REVIEW_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(document_review, "async_session_factory", lambda: _SessionScope(session))
    monkeypatch.setattr(document_review, "TaskStore", lambda _redis: store)
    monkeypatch.setattr(document_review, "get_redis", lambda: object())
    monkeypatch.setattr(document_review, "llm_chat_completion", provider)

    await document_review.run_document_review_background(
        task_id=review.task_id, review_id=review.id, user_id=1
    )

    assert review.state == "failed"
    assert review.failure_code == "timeout"
    assert review.findings == []
    assert review.model_name is None
    assert store.data[review.task_id] == {
        "user_id": 1,
        "status": "failed",
        "message": "Review unavailable",
        "review_id": review.id,
    }
    late_release.set()
    await asyncio.wait_for(late_finished.wait(), timeout=1)
    assert review.state == "failed"
    assert review.findings == []
    assert review.model_name is None
    assert store.data[review.task_id]["status"] == "failed"


@pytest.mark.asyncio
async def test_worker_records_safe_provider_call_diagnostics_without_public_detail_leakage(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="provider-failure")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        raise LLMUnavailableError(
            "SECRET_PROVIDER_BODY_SENTINEL",
            provider_failure_category="rate_limit",
            providers_exhausted=True,
            attempted_provider_names=("openai", "deepseek"),
            attempt_categories=("rate_limit", "server_error"),
        )

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    fields = _event_fields(diagnostic_logger)
    assert fields == {
        "review_id": review.id,
        "task_id": review.task_id,
        "stage": "provider_call",
        "exception_class": "LLMUnavailableError",
        "package_id": package.id,
        "provider_failure_category": "rate_limit",
        "providers_exhausted": True,
        "attempt_count": 2,
        "attempted_provider_names": ("openai", "deepseek"),
        "attempt_categories": ("rate_limit", "server_error"),
        "generation_attempt": 1,
        "corrective_regeneration": False,
    }
    assert review.failure_code == "unavailable"
    assert review.failure_message == "Review unavailable"
    assert store.data[review.task_id]["message"] == "Review unavailable"
    assert "SECRET_" not in str(
        (diagnostic_logger.events, review.failure_message, store.data[review.task_id])
    )
    assert review.diagnostic_summary == {
        "diagnostic_version": "document-review-diagnostic-v1",
        "review_id": review.id,
        "task_id": review.task_id,
        "outcome": "failed",
        "generations": [
            {
                "generation_attempt": 1,
                "stage": "provider_call",
                "provider_failure_category": "rate_limit",
                "provider_attempt_count": 2,
                "providers_exhausted": True,
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_category"),
    [
        (_provider_status_error(401, AuthenticationError), "auth_error"),
        (_provider_status_error(400, BadRequestError), "other_http_error"),
    ],
)
async def test_worker_safely_classifies_propagated_non_retryable_provider_errors(
    monkeypatch: MonkeyPatch,
    error: APIStatusError,
    expected_category: str,
) -> None:
    package = _worker_package()
    review = _review(package, review_id=f"provider-{expected_category}")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        raise error

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    fields = _event_fields(diagnostic_logger)
    assert fields == {
        "review_id": review.id,
        "task_id": review.task_id,
        "stage": "provider_call",
        "exception_class": type(error).__name__,
        "package_id": package.id,
        "provider_failure_category": expected_category,
        "generation_attempt": 1,
        "corrective_regeneration": False,
    }
    assert review.failure_code == "unavailable"
    assert review.failure_message == "Review unavailable"
    assert store.data[review.task_id]["message"] == "Review unavailable"
    assert "SECRET_" not in str(
        (diagnostic_logger.events, review.failure_message, store.data[review.task_id])
    )


@pytest.mark.asyncio
async def test_worker_classifies_response_extraction_failure_without_content_leakage(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    package.content["resume"] = {
        "content": {"summary": "SECRET_RESUME_SENTINEL"},
        "provenance": {"source_revision_sha256": "a"},
    }
    package.content["cover_letter"] = {"content": "SECRET_COVER_LETTER_SENTINEL"}
    encoded = json.dumps(package.content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    package.content_sha256 = hashlib.sha256(encoded.encode()).hexdigest()
    review = _review(package, review_id="extract")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace())]), "fake-model"

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    fields = _event_fields(diagnostic_logger)
    assert fields["stage"] == "provider_response_extraction"
    assert fields["response_type"] == "SimpleNamespace"
    assert fields["choices_count"] == 1
    assert review.failure_code == "unavailable"
    assert review.failure_message == "Review unavailable"
    assert store.data[review.task_id]["message"] == "Review unavailable"
    assert "SECRET_" not in str(
        (
            diagnostic_logger.events,
            review.failure_message,
            store.data[review.task_id],
            review.findings,
        )
    )


@pytest.mark.asyncio
async def test_worker_classifies_malformed_json_without_content_leakage(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="json")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    raw = "SECRET_PROVIDER_CONTENT_SENTINEL"

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=raw))]
        ), "fake-model"

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    fields = _event_fields(diagnostic_logger)
    assert fields["stage"] == "response_json_parse"
    assert fields["content_length"] == len(raw)
    assert review.failure_code == "unavailable"
    assert review.findings == []
    assert "SECRET_PROVIDER_CONTENT_SENTINEL" not in str(
        (
            diagnostic_logger.events,
            review.failure_message,
            store.data[review.task_id],
            review.findings,
        )
    )


@pytest.mark.asyncio
async def test_worker_classifies_schema_validation_failure(monkeypatch: MonkeyPatch) -> None:
    package = _worker_package()
    review = _review(package, review_id="schema")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps({"findings": [{"id": "f1"}]}))
                )
            ]
        ), "fake-model"

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    failure_events = [
        fields for event, fields in diagnostic_logger.events if event == "document_review.failed"
    ]
    assert len(failure_events) == 1
    fields = failure_events[0]
    assert fields["stage"] == "response_schema_validation"
    assert fields["exception_class"] == "ValidationError"
    assert isinstance(fields["validation_error_count"], int)
    assert [event["generation"] for event in _schema_failure_events(diagnostic_logger)] == [1, 2]
    assert review.failure_code == "unavailable"


@pytest.mark.asyncio
async def _legacy_test_worker_classifies_evidence_validation_failure_without_content_leakage(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="evidence")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    invalid = _finding(
        excerpt="SECRET_EVIDENCE_SENTINEL",
        evidence=[
            {
                "role": "target",
                "document": "resume",
                "locator": "/resume/content/summary",
                "excerpt": "SECRET_EVIDENCE_SENTINEL",
            }
        ],
    )

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps({"findings": [invalid]}))
                )
            ]
        ), "fake-model"

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    fields = _event_fields(diagnostic_logger)
    assert fields["stage"] == "evidence_validation"
    assert fields["evidence_reason"] == "excerpt_mismatch"
    assert review.failure_code == "unavailable"
    assert "SECRET_EVIDENCE_SENTINEL" not in str(
        (
            diagnostic_logger.events,
            review.failure_message,
            store.data[review.task_id],
            review.findings,
        )
    )


@pytest.mark.asyncio
async def _legacy_test_worker_rejects_container_pointer_as_non_string_leaf(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _current_shape_worker_package()
    review = _review(package, review_id="container")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    invalid = _finding(
        locator="/resume/content/work_history",
        excerpt="Backend Engineer",
        evidence=[
            {
                "role": "target",
                "document": "resume",
                "locator": "/resume/content/work_history",
                "excerpt": "Backend Engineer",
            }
        ],
    )

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps({"findings": [invalid]}))
                )
            ]
        ), "fake-model"

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    fields = _event_fields(diagnostic_logger)
    assert fields["stage"] == "evidence_validation"
    assert fields["evidence_reason"] == "pointer_not_string_leaf"


@pytest.mark.asyncio
async def _legacy_test_worker_emits_finding_pointer_failure_metadata_without_locator_or_content_leakage(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="finding-pointer")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    sentinel = "SECRET_POINTER_SEGMENT_DO_NOT_LOG"
    invalid = _finding(locator=f"/resume/content/{sentinel}/foo")

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps({"findings": [invalid]}))
                )
            ]
        ), "fake-model"

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    assert len(diagnostic_logger.events) == 2
    fields = diagnostic_logger.events[-1][1]
    assert fields["stage"] == "evidence_validation"
    assert fields["evidence_reason"] == "invalid_pointer"
    assert fields["pointer_failure_kind"] == "missing_key"
    assert fields["document"] == "resume"
    assert fields["locator_role"] == "finding"
    assert fields["pointer_depth"] == 4
    assert fields["failing_segment_position"] == 2
    assert fields["container_kind"] == "object"
    rendered = str(
        (
            diagnostic_logger.events,
            review.failure_message,
            store.data[review.task_id],
            review.findings,
        )
    )
    assert sentinel not in rendered
    assert f"/resume/content/{sentinel}/foo" not in rendered
    assert "Built APIs" not in rendered
    assert "Requires Python" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("locator", "failure_kind", "depth", "position", "container_kind"),
    [
        ("/resume/content/nested/not-an-index", "invalid_array_index", 4, 3, "array"),
        ("/resume/content/nested/9", "array_index_out_of_range", 4, 3, "array"),
        ("/resume/content/summary/extra", "traversal_type_mismatch", 4, 3, "scalar"),
    ],
)
async def _legacy_test_worker_emits_evidence_pointer_failure_metadata(
    monkeypatch: MonkeyPatch,
    locator: str,
    failure_kind: str,
    depth: int,
    position: int,
    container_kind: str,
) -> None:
    package = _worker_package()
    review = _review(package, review_id=f"evidence-pointer-{failure_kind}")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    invalid = _finding(
        evidence=[
            {
                "role": "target",
                "document": "resume",
                "locator": locator,
                "excerpt": "Built APIs",
            }
        ]
    )

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps({"findings": [invalid]}))
                )
            ]
        ), "fake-model"

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    assert len(diagnostic_logger.events) == 2 if failure_kind != "traversal_type_mismatch" else 1
    fields = diagnostic_logger.events[-1][1]
    assert fields["stage"] == "evidence_validation"
    assert fields["evidence_reason"] == "invalid_pointer"
    assert fields["pointer_failure_kind"] == failure_kind
    assert fields["document"] == "resume"
    assert fields["locator_role"] == "evidence"
    assert fields["pointer_depth"] == depth
    assert fields["failing_segment_position"] == position
    assert fields["container_kind"] == container_kind
    assert locator not in str(diagnostic_logger.events)


@pytest.mark.asyncio
async def test_worker_classifies_completion_persistence_failure(monkeypatch: MonkeyPatch) -> None:
    package = _worker_package()
    review = _review(package, review_id="persistence")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    session = _WorkerSession(review, package, fail_completion_commit=True)

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"findings": []}'))]
        ), "fake-model"

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store, session=session
    )
    await _run_worker(review)

    assert diagnostic_logger.events == [("document_review.diagnostic_persistence_failed", {})]
    assert review.state == "completed"
    assert review.failure_code is None
    assert review.diagnostic_summary is None
    assert store.data[review.task_id]["status"] == "done"
    assert session.rollback_count == 1


@pytest.mark.asyncio
async def test_worker_classifies_task_completion_failure(monkeypatch: MonkeyPatch) -> None:
    package = _worker_package()
    review = _review(package, review_id="task")
    store = _MemoryTaskStore(fail_done_update=True)
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"findings": []}'))]
        ), "fake-model"

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    fields = _event_fields(diagnostic_logger)
    assert fields["stage"] == "task_completion"
    assert review.state == "completed"
    assert store.data[review.task_id]["status"] == "running"


@pytest.mark.asyncio
async def test_worker_uses_worker_unknown_for_pre_provider_failures(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="pre-provider")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}

    async def fail_validation(self: object, package: object) -> None:
        del self, package
        raise RuntimeError("SECRET_PRE_PROVIDER_SENTINEL")

    monkeypatch.setattr(document_review.DocumentReviewService, "_validate_package", fail_validation)
    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=object(), store=store
    )
    await _run_worker(review)

    fields = _event_fields(diagnostic_logger)
    assert fields["stage"] == "worker_unknown"
    assert fields["exception_class"] == "RuntimeError"
    assert "SECRET_" not in str(
        (diagnostic_logger.events, review.failure_message, store.data[review.task_id])
    )


@pytest.mark.asyncio
async def _legacy_test_worker_valid_response_completes_without_diagnostic_event(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="success")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps({"findings": [_finding()]}))
                )
            ]
        ), "fake-model"

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    assert diagnostic_logger.events == []
    assert review.state == "completed"
    assert review.findings == [_finding()]
    assert store.data[review.task_id] == {
        "user_id": 1,
        "status": "done",
        "message": "Document review completed",
        "review_id": review.id,
    }


@pytest.mark.asyncio
async def _legacy_test_worker_completes_with_current_shape_string_leaf_locators(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _current_shape_worker_package()
    review = _review(package, review_id="current-shape-success")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    findings = [
        {
            "id": "job-match",
            "document": "resume",
            "category": "job_document_mismatch",
            "severity": "suggestion",
            "excerpt": "Built API services",
            "locator": "/resume/content/canonical_sections/0/items/0/text",
            "rationale": "Make the Python experience easier to find.",
            "evidence": [
                {
                    "role": "requirement",
                    "document": "job",
                    "locator": "/job/description",
                    "excerpt": "Requires Python",
                },
                {
                    "role": "target",
                    "document": "resume",
                    "locator": "/resume/content/canonical_sections/0/items/0/text",
                    "excerpt": "Built API services",
                },
            ],
            "proposed_action": "Clarify the relevant experience.",
        },
        {
            "id": "highlight",
            "document": "resume",
            "category": "clarity",
            "severity": "suggestion",
            "excerpt": "Automated deployment checks",
            "locator": "/resume/content/work_history/0/tailored_highlights/0",
            "rationale": "State the outcome more clearly.",
            "evidence": [
                {
                    "role": "target",
                    "document": "resume",
                    "locator": "/resume/content/work_history/0/tailored_highlights/0",
                    "excerpt": "Automated deployment checks",
                }
            ],
            "proposed_action": "Add a concrete outcome if verified.",
        },
    ]

    calls = 0

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=json.dumps({"findings": findings})))
            ]
        ), "fake-model"

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    assert diagnostic_logger.events == []
    assert review.state == "completed"
    assert review.findings == findings
    assert store.data[review.task_id]["status"] == "done"
    assert calls == 1
    assert review.diagnostic_summary == {
        "diagnostic_version": "document-review-diagnostic-v1",
        "review_id": review.id,
        "task_id": review.task_id,
        "outcome": "completed",
        "generation_count": 1,
        "completed_on_generation": 1,
    }


def _legacy_test_review_policy_version_is_v3() -> None:
    assert REVIEW_POLICY_VERSION == "application-document-review-v3"


def _legacy_test_corrective_messages_include_only_safe_feedback() -> None:
    messages = corrective_review_messages(projection=_package(), reason="invalid_evidence_id")
    feedback = messages[1]["content"]
    assert "invalid_pointer" in feedback
    assert "missing_key" in feedback
    assert "resume" in feedback
    assert "finding" in feedback
    assert "Built APIs" not in feedback
    assert "/resume/content/SECRET_POINTER" not in feedback


@pytest.mark.parametrize(
    ("reason", "pointer_failure_kind", "expected"),
    [
        ("invalid_pointer", "missing_key", True),
        ("invalid_pointer", "invalid_array_index", True),
        ("invalid_pointer", "array_index_out_of_range", True),
        ("invalid_pointer", "missing_leading_slash", False),
        ("invalid_pointer", "traversal_type_mismatch", False),
        ("invalid_pointer", "resolved_null", False),
        ("invalid_pointer", "unknown_pointer_failure", False),
        ("pointer_not_string_leaf", None, False),
        ("excerpt_mismatch", None, False),
        ("invalid_document", None, False),
        ("duplicate_evidence_role", None, False),
        ("missing_required_evidence", None, False),
        ("required_role_mismatch", None, False),
    ],
)
def _legacy_test_corrective_regeneration_eligibility_is_closed(
    reason: str,
    pointer_failure_kind: str | None,
    expected: bool,
) -> None:
    metadata: dict[str, object] | None = (
        {"pointer_failure_kind": pointer_failure_kind} if pointer_failure_kind else None
    )
    exc = document_review._EvidenceValidationError(reason, structural_metadata=metadata)
    assert document_review._is_corrective_regeneration_eligible(exc) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pointer",
    [
        "/resume/content/missing",
        "/resume/content/nested/not-an-index",
        "/resume/content/nested/9",
    ],
)
async def _legacy_test_worker_corrects_allowlisted_pointer_failures_once(
    monkeypatch: MonkeyPatch,
    pointer: str,
) -> None:
    package = _worker_package()
    review = _review(package, review_id=f"correct-{pointer.rsplit('/', 1)[-1]}")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    invalid = _finding(locator=pointer)
    calls: list[dict[str, object]] = []

    async def provider(**kwargs: object) -> tuple[Any, str]:
        calls.append(kwargs)
        payload = {"findings": [invalid]} if len(calls) == 1 else {"findings": [_finding()]}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
        ), "fake-model"

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    original_hash = package.content_sha256
    original_content = json.dumps(package.content, sort_keys=True)
    await _run_worker(review)

    assert len(calls) == 2
    assert (
        cast(list[object], calls[0]["messages"])[-1] == cast(list[object], calls[1]["messages"])[-1]
    )
    assert review.id == f"correct-{pointer.rsplit('/', 1)[-1]}"
    assert review.task_id in store.data
    assert len(store.data) == 1
    assert store.data[review.task_id]["review_id"] == review.id
    assert review.state == "completed"
    assert package.content_sha256 == original_hash
    assert json.dumps(package.content, sort_keys=True) == original_content
    fields = diagnostic_logger.events[0][1]
    assert fields["stage"] == "corrective_regeneration"
    assert fields["generation_attempt"] == 1
    assert fields["corrective_regeneration"] is True
    assert review.diagnostic_summary == {
        "diagnostic_version": "document-review-diagnostic-v1",
        "review_id": review.id,
        "task_id": review.task_id,
        "outcome": "completed",
        "generation_count": 2,
        "completed_on_generation": 2,
    }


@pytest.mark.asyncio
async def _legacy_test_worker_stops_after_second_eligible_pointer_failure(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="two-only")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    calls = 0

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise AssertionError("third generation attempted")
        invalid = _finding(locator="/resume/content/missing")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps({"findings": [invalid]}))
                )
            ]
        ), "fake-model"

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)
    assert calls == 2
    assert review.state == "failed"
    assert diagnostic_logger.events[-1][1]["generation_attempt"] == 2
    assert (
        len(cast(list[object], cast(dict[str, object], review.diagnostic_summary)["generations"]))
        == 2
    )


@pytest.mark.asyncio
async def _legacy_test_worker_second_provider_failure_is_terminal_safe_and_distinct_from_provider_attempts(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="second-provider-failure")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    calls: list[dict[str, object]] = []
    rejected_locator = "/resume/content/SECRET_REJECTED_LOCATOR"
    rejected_excerpt = "SECRET_REJECTED_EXCERPT"
    rejected_finding = "SECRET_REJECTED_FINDING"
    first = _finding(
        locator=rejected_locator,
        excerpt=rejected_excerpt,
        rationale=rejected_finding,
    )

    async def provider(**kwargs: object) -> tuple[Any, str]:
        calls.append(kwargs)
        if len(calls) == 1:
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps({"findings": [first]}),
                        )
                    )
                ]
            ), "fake-model"
        if len(calls) == 2:
            raise LLMUnavailableError(
                "SECRET_PROVIDER_ERROR_BODY",
                provider_failure_category="rate_limit",
                providers_exhausted=True,
                attempted_provider_names=("openai", "deepseek", "fallback"),
                attempt_categories=("rate_limit", "server_error", "timeout_or_connection"),
            )
        raise AssertionError("third generation attempted")

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    assert len(calls) == 2
    assert review.state == "failed"
    assert review.failure_code == "unavailable"
    assert review.failure_message == "Review unavailable"
    fields = diagnostic_logger.events[-1][1]
    assert fields["stage"] == "provider_call"
    assert fields["generation_attempt"] == 2
    assert fields["attempt_count"] == 3
    rendered = str(
        (
            calls[1]["messages"],
            diagnostic_logger.events,
            review.failure_message,
            store.data,
            review.diagnostic_summary,
        )
    )
    for sentinel in (
        rejected_locator,
        rejected_excerpt,
        rejected_finding,
        "SECRET_PROVIDER_ERROR_BODY",
    ):
        assert sentinel not in rendered
    assert review.diagnostic_summary == {
        "diagnostic_version": "document-review-diagnostic-v1",
        "review_id": review.id,
        "task_id": review.task_id,
        "outcome": "failed",
        "generations": [
            {
                "generation_attempt": 1,
                "stage": "evidence_validation",
                "reason": "invalid_pointer",
                "pointer_failure_kind": "missing_key",
                "document": "resume",
                "locator_role": "finding",
            },
            {
                "generation_attempt": 2,
                "stage": "provider_call",
                "provider_failure_category": "rate_limit",
                "provider_attempt_count": 3,
                "providers_exhausted": True,
            },
        ],
    }


@pytest.mark.asyncio
async def _legacy_test_worker_second_malformed_json_is_terminal_without_raw_response_leakage(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="second-malformed-json")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    calls = 0
    malformed = "SECRET_SECOND_MALFORMED_RESPONSE"

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            payload = {"findings": [_finding(locator="/resume/content/missing")]}
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            ), "fake-model"
        if calls == 2:
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=malformed))]
            ), "fake-model"
        raise AssertionError("third generation attempted")

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    assert calls == 2
    assert review.state == "failed"
    assert review.failure_code == "unavailable"
    assert review.failure_message == "Review unavailable"
    fields = diagnostic_logger.events[-1][1]
    assert fields["stage"] == "response_json_parse"
    assert fields["generation_attempt"] == 2
    assert malformed not in str(
        (diagnostic_logger.events, review.failure_message, store.data, review.findings)
    )


@pytest.mark.asyncio
async def _legacy_test_worker_second_schema_failure_is_terminal_without_raw_response_leakage(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="second-schema")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    calls = 0
    schema_invalid = {"findings": [{"id": "SECRET_SECOND_SCHEMA_RESPONSE"}]}

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            payload = {"findings": [_finding(locator="/resume/content/missing")]}
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            ), "fake-model"
        if calls == 2:
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(schema_invalid),
                        )
                    )
                ]
            ), "fake-model"
        raise AssertionError("third generation attempted")

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    assert calls == 2
    assert review.state == "failed"
    assert review.failure_code == "unavailable"
    fields = diagnostic_logger.events[-1][1]
    assert fields["stage"] == "response_schema_validation"
    assert fields["generation_attempt"] == 2
    assert isinstance(fields["validation_error_count"], int)
    assert "SECRET_SECOND_SCHEMA_RESPONSE" not in str(
        (diagnostic_logger.events, review.failure_message, store.data, review.findings)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("locator", ["resume/content/summary", "/resume/content/summary/extra"])
async def _legacy_test_worker_does_not_correct_noneligible_pointer_failures(
    monkeypatch: MonkeyPatch,
    locator: str,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="noneligible")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    calls = 0

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        calls += 1
        invalid = _finding(locator=locator)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps({"findings": [invalid]}))
                )
            ]
        ), "fake-model"

    _install_worker(monkeypatch, review=review, package=package, provider=provider, store=store)
    await _run_worker(review)
    assert calls == 1
    assert review.state == "failed"
    summary = cast(dict[str, object], review.diagnostic_summary)
    assert summary["outcome"] == "failed"
    assert len(cast(list[object], summary["generations"])) == 1


def test_durable_diagnostic_builder_is_closed_and_historical_null_is_valid() -> None:
    summary = document_review._generation_summary(
        generation_attempt=1,
        stage="evidence_validation",
        reason="invalid_pointer",
        structural_metadata={
            "pointer_failure_kind": "missing_key",
            "document": "resume",
            "locator_role": "finding",
            "locator": "SECRET_RAW_LOCATOR",
            "excerpt": "SECRET_EXCERPT",
            "unknown": "SECRET_DYNAMIC",
        },
        provider_failure_category="rate_limit",
        provider_attempt_count=2,
        providers_exhausted=True,
    )
    assert summary == {
        "generation_attempt": 1,
        "stage": "evidence_validation",
        "reason": "invalid_pointer",
        "pointer_failure_kind": "missing_key",
        "document": "resume",
        "locator_role": "finding",
        "provider_failure_category": "rate_limit",
        "provider_attempt_count": 2,
        "providers_exhausted": True,
    }
    assert "SECRET_" not in json.dumps(summary)
    review = _review(_worker_package())
    assert review.diagnostic_summary is None
    assert "diagnostic_summary" not in DocumentReviewResponse.model_fields


@pytest.mark.asyncio
async def _legacy_test_second_generation_uses_remaining_shared_budget(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="shared-budget")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    time_values = iter((0.0, 0.0, 130.0))
    timeouts: list[float] = []

    async def provider(**kwargs: object) -> tuple[Any, str]:
        timeouts.append(cast(float, kwargs["timeout"]))
        invalid = _finding(locator="/resume/content/missing")
        payload = {"findings": [invalid]} if len(timeouts) == 1 else {"findings": [_finding()]}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
        ), "fake-model"

    _install_worker(monkeypatch, review=review, package=package, provider=provider, store=store)
    monkeypatch.setattr(document_review, "monotonic", lambda: next(time_values))
    await _run_worker(review)
    assert timeouts == [60.0, 50.0]


@pytest.mark.asyncio
async def _legacy_test_worker_does_not_invoke_second_generation_after_shared_deadline_expires(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="expired-before-second")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    time_values = iter((0.0, 0.0, 180.0))
    calls = 0
    original_hash = package.content_sha256
    original_content = json.dumps(package.content, sort_keys=True)

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("second generation invoked after deadline exhaustion")
        payload = {"findings": [_finding(locator="/resume/content/missing")]}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
        ), "fake-model"

    _install_worker(monkeypatch, review=review, package=package, provider=provider, store=store)
    monkeypatch.setattr(document_review, "monotonic", lambda: next(time_values))
    await _run_worker(review)

    assert calls == 1
    assert review.state == "failed"
    assert review.failure_code == "timeout"
    assert review.failure_message == "Review timed out"
    assert review.id == "expired-before-second"
    assert len(store.data) == 1
    assert store.data[review.task_id]["review_id"] == review.id
    assert package.content_sha256 == original_hash
    assert json.dumps(package.content, sort_keys=True) == original_content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "pointer_failure_kind"),
    [
        ("invalid_document", None),
        ("pointer_not_string_leaf", None),
        ("excerpt_mismatch", None),
        ("duplicate_evidence_role", None),
        ("missing_required_evidence", None),
        ("required_role_mismatch", None),
        ("invalid_pointer", "missing_leading_slash"),
        ("invalid_pointer", "traversal_type_mismatch"),
        ("invalid_pointer", "resolved_null"),
        ("invalid_pointer", "unknown_pointer_failure"),
    ],
)
async def _legacy_test_worker_does_not_regenerate_for_closed_eligibility_failures(
    monkeypatch: MonkeyPatch,
    reason: str,
    pointer_failure_kind: str | None,
) -> None:
    package = _worker_package()
    review = _review(package, review_id=f"closed-{reason}-{pointer_failure_kind}")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    calls = 0

    def reject_with_closed_failure(
        _parsed: object, _package: dict[str, object]
    ) -> list[dict[str, object]]:
        metadata: dict[str, object] | None = (
            {"pointer_failure_kind": pointer_failure_kind} if pointer_failure_kind else None
        )
        raise document_review._EvidenceValidationError(reason, structural_metadata=metadata)

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("unexpected corrective generation")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps({"findings": []}),
                    )
                )
            ]
        ), "fake-model"

    _install_worker(monkeypatch, review=review, package=package, provider=provider, store=store)
    monkeypatch.setattr(
        document_review, "_validate_structured_findings", reject_with_closed_failure
    )
    await _run_worker(review)

    assert calls == 1
    assert review.state == "failed"


def _provider_finding(
    *, evidence_ids: list[str], category: str = "repetition"
) -> dict[str, object]:
    finding: dict[str, object] = {
        "id": "v4-finding",
        "category": category,
        "severity": "suggestion",
        "rationale": "Repeated wording",
        "proposed_action": "Vary the wording",
    }
    if category in {"unsupported_claim", "repetition", "clarity", "keyword_stuffing"}:
        finding["target_evidence_id"] = evidence_ids[0]
    elif category == "cross_document_mismatch":
        finding["resume_evidence_id"] = next(
            evidence_id for evidence_id in evidence_ids if evidence_id.startswith("resume-")
        )
        finding["cover_letter_evidence_id"] = next(
            evidence_id for evidence_id in evidence_ids if evidence_id.startswith("cover-")
        )
    else:
        finding["job_evidence_id"] = next(
            evidence_id for evidence_id in evidence_ids if evidence_id.startswith("job-")
        )
        finding["target_evidence_id"] = next(
            evidence_id
            for evidence_id in evidence_ids
            if evidence_id.startswith(("resume-", "cover-"))
        )
    return finding


def _provider_response(findings: list[dict[str, object]]) -> ProviderDocumentReviewResponseV10:
    return ProviderDocumentReviewResponseV10.model_validate({"findings": findings})


def _evidence_id(package: dict[str, object], locator: str) -> str:
    catalog, _projection = document_review._build_evidence_catalog_and_projection(package)
    return next(entry.evidence_id for entry in catalog.values() if entry.locator == locator)


def test_v6_prompt_uses_id_only_evidence_contract() -> None:
    catalog, projection = document_review._build_evidence_catalog_and_projection(_package())
    prompt = review_messages(projection=projection)[0]["content"]

    assert (
        catalog and "evidence_id" in prompt and "Select only evidence IDs visibly present" in prompt
    )
    assert "LOCATOR CONTRACT" not in prompt and "Representative current package paths" not in prompt
    assert "locator" in prompt  # It explicitly prohibits provider-owned locators.
    assert "Do not assign roles and do not return document, locator, excerpt" in prompt


def test_v10_prompt_has_category_specific_evidence_slots() -> None:
    prompt = review_messages(projection={})[0]["content"]

    assert REVIEW_POLICY_VERSION == "application-document-review-v10"
    for slot in (
        "target_evidence_id",
        "resume_evidence_id",
        "cover_letter_evidence_id",
        "job_evidence_id",
    ):
        assert slot in prompt
    for namespace in ("resume-e...", "cover-e...", "job-e..."):
        assert namespace in prompt
    assert "finding document" not in prompt.lower()


def test_v4_catalog_projection_is_deterministic_and_escapes_pointers() -> None:
    package: dict[str, object] = {
        "job": {"z": "z", "a/b": ["first", "", None, True, 7, {"a~b": "last"}]},
        "resume": {"content": {"duplicate": "z"}},
        "cover_letter": {"content": "letter"},
        "manual_questions": [{"answer": "must not appear"}],
    }
    first_catalog, projection = document_review._build_evidence_catalog_and_projection(package)
    second_catalog, second_projection = document_review._build_evidence_catalog_and_projection(
        package
    )

    assert list(first_catalog) == [
        "cover-e0001",
        "job-e0001",
        "job-e0002",
        "job-e0003",
        "resume-e0001",
    ]
    assert first_catalog == second_catalog and projection == second_projection
    assert first_catalog["job-e0001"].locator == "/job/a~1b/0"
    assert first_catalog["job-e0002"].locator == "/job/a~1b/5/a~0b"
    assert first_catalog["job-e0003"].text == first_catalog["resume-e0001"].text == "z"
    assert "manual_questions" not in projection
    assert projection["job"] == {
        "a/b": [
            {"evidence_id": "job-e0001", "text": "first"},
            "",
            None,
            True,
            7,
            {"a~b": {"evidence_id": "job-e0002", "text": "last"}},
        ],
        "z": {"evidence_id": "job-e0003", "text": "z"},
    }


def test_v4_ids_expand_beyond_four_digits() -> None:
    package: dict[str, object] = {
        "job": {"leaves": [str(index) for index in range(10_000)]},
        "resume": {},
        "cover_letter": {},
    }
    catalog, _projection = document_review._build_evidence_catalog_and_projection(package)
    assert list(catalog)[0] == "job-e0001"
    assert list(catalog)[-1] == "job-e10000"


@pytest.mark.parametrize(
    ("category", "evidence", "document", "locator"),
    [
        (
            "repetition",
            [("target", "/resume/content/summary")],
            "resume",
            "/resume/content/summary",
        ),
        (
            "cross_document_mismatch",
            [("target", "/resume/content/summary"), ("comparison", "/cover_letter/content")],
            "resume",
            "/resume/content/summary",
        ),
        (
            "job_document_mismatch",
            [("requirement", "/job/description"), ("target", "/resume/content/summary")],
            "resume",
            "/resume/content/summary",
        ),
        (
            "omitted_verified_evidence",
            [("requirement", "/job/description"), ("target", "/cover_letter/content")],
            "cover_letter",
            "/cover_letter/content",
        ),
    ],
)
def test_v4_canonicalizes_target_and_role_matrix(
    category: str,
    evidence: list[tuple[str, str]],
    document: str,
    locator: str,
) -> None:
    package = _package()
    catalog, _projection = document_review._build_evidence_catalog_and_projection(package)
    provider = _provider_finding(
        category=category,
        evidence_ids=[_evidence_id(package, path) for _role, path in evidence],
    )
    result = document_review._canonicalize_provider_review(_provider_response([provider]), catalog)[
        0
    ]
    assert result["document"] == document and result["locator"] == locator
    assert result["excerpt"] == catalog[_evidence_id(package, locator)].text
    assert all(
        "evidence_id" not in item for item in cast(list[dict[str, object]], result["evidence"])
    )


@pytest.mark.parametrize(
    ("provider", "reason"),
    [
        (_provider_finding(evidence_ids=["resume-e9999"]), "invalid_evidence_id"),
    ],
)
def test_v4_rejects_invalid_evidence_references(provider: dict[str, object], reason: str) -> None:
    catalog, _projection = document_review._build_evidence_catalog_and_projection(_package())
    with pytest.raises(document_review._EvidenceValidationError, match=reason):
        document_review._canonicalize_provider_review(_provider_response([provider]), catalog)


@pytest.mark.skip(reason="V10 rejects document-slot mismatches at the provider schema boundary")
def test_v6_rejects_disallowed_document_with_safe_context() -> None:
    package = _package()
    catalog, _projection = document_review._build_evidence_catalog_and_projection(package)
    provider = _provider_finding(
        category="job_document_mismatch",
        evidence_ids=[_evidence_id(package, "/resume/content/summary")],
    )
    with pytest.raises(
        document_review._EvidenceValidationError, match="missing_required_evidence"
    ) as exc_info:
        document_review._canonicalize_provider_review(_provider_response([provider]), catalog)
    assert exc_info.value.structural_metadata == {
        "category": "job_document_mismatch",
        "required_documents": ("cover_letter", "job", "resume"),
        "actual_documents": ("resume",),
    }


def test_v6_document_feedback_is_structural_and_content_free() -> None:
    messages = corrective_review_messages(
        projection={"resume": {"evidence_id": "SECRET_EVIDENCE_ID", "text": "SECRET_SOURCE_TEXT"}},
        reason="invalid_evidence_document",
        category="cross_document_mismatch",
        actual_documents=("job", "resume"),
    )
    feedback = messages[1]["content"]

    for value in ("cross_document_mismatch", "job", "resume", "cover_letter"):
        assert value in feedback
    for sentinel in (
        "SECRET_EVIDENCE_ID",
        "SECRET_SOURCE_TEXT",
        "SECRET_LOCATOR",
        "SECRET_EXCERPT",
        "SECRET_RATIONALE",
        "SECRET_ACTION",
    ):
        assert sentinel not in feedback


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("category", "invalid_locators", "expected_required", "expected_actual"),
    [
        (
            "unsupported_claim",
            ["/job/description"],
            "exactly one evidence item from resume OR cover_letter",
            "job",
        ),
        (
            "repetition",
            ["/job/description"],
            "exactly one evidence item from resume OR cover_letter",
            "job",
        ),
        (
            "clarity",
            ["/job/description"],
            "exactly one evidence item from resume OR cover_letter",
            "job",
        ),
        (
            "keyword_stuffing",
            ["/job/description"],
            "exactly one evidence item from resume OR cover_letter",
            "job",
        ),
        (
            "cross_document_mismatch",
            ["/resume/content/summary", "/resume/content/nested/0"],
            "exactly one resume evidence item AND exactly one cover_letter evidence item",
            "resume + resume",
        ),
        (
            "job_document_mismatch",
            ["/resume/content/summary", "/cover_letter/content"],
            "exactly one job evidence item AND exactly one evidence item from resume OR cover_letter",
            "cover_letter + resume",
        ),
        (
            "omitted_verified_evidence",
            ["/resume/content/summary", "/cover_letter/content"],
            "exactly one job evidence item AND exactly one evidence item from resume OR cover_letter",
            "cover_letter + resume",
        ),
    ],
)
@pytest.mark.skip(
    reason="V10 structurally blocks these provider compositions; prompt feedback remains covered directly"
)
async def test_v9_worker_evidence_correction_preserves_category_composition_and_is_content_safe(
    monkeypatch: MonkeyPatch,
    category: str,
    invalid_locators: list[str],
    expected_required: str,
    expected_actual: str,
) -> None:
    package = _worker_package()
    content = cast(dict[str, Any], package.content)
    content["job"] = {
        "description": "SECRET_SOURCE_TEXT_SENTINEL",
        "SECRET_LOCATOR_SENTINEL": "SECRET_SOURCE_TEXT_SENTINEL",
    }
    content["resume"] = {
        "content": {
            "summary": "SECRET_EXCERPT_SENTINEL",
            "nested": ["SECRET_SOURCE_TEXT_SENTINEL"],
        },
        "provenance": {"source_revision_sha256": "a"},
    }
    content["cover_letter"] = {"content": "SECRET_SOURCE_TEXT_SENTINEL"}
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    package.content_sha256 = hashlib.sha256(encoded.encode()).hexdigest()
    review = _review(package, review_id=f"v9-{category}")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    valid_ids = [_evidence_id(package.content, "/resume/content/summary")]
    if category == "cross_document_mismatch":
        valid_ids.append(_evidence_id(package.content, "/cover_letter/content"))
    elif category in {"job_document_mismatch", "omitted_verified_evidence"}:
        valid_ids.insert(0, _evidence_id(package.content, "/job/description"))
    invalid_ids = [_evidence_id(package.content, locator) for locator in invalid_locators]
    responses = [
        {
            "findings": [
                {
                    **_provider_finding(category=category, evidence_ids=invalid_ids),
                    "id": "SECRET_RAW_PROVIDER_SENTINEL",
                    "rationale": "SECRET_RATIONALE_SENTINEL",
                    "proposed_action": "SECRET_ACTION_SENTINEL",
                }
            ]
        },
        {"findings": [_provider_finding(category=category, evidence_ids=valid_ids)]},
    ]
    calls = 0

    async def provider(**kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        response = responses[calls]
        calls += 1
        if calls == 2:
            feedback = cast(list[dict[str, str]], kwargs["messages"])[1]["content"]
            assert f"Category: {category}." in feedback
            assert (
                f"Required evidence count: exactly {1 if category in {'unsupported_claim', 'repetition', 'clarity', 'keyword_stuffing'} else 2} evidence item"
                in feedback
            )
            assert expected_required in feedback
            assert f"Actual selected document composition: {expected_actual}." in feedback
            assert "Regenerate the COMPLETE response." in feedback
            assert (
                "Choose only visible evidence IDs from the required document groups above."
                in feedback
            )
            assert (
                "Replace the invalid document composition; do not reuse that invalid composition."
                in feedback
            )
            assert "cover_letter or job or resume" not in feedback
            for sentinel in (
                "SECRET_EVIDENCE_ID",
                "SECRET_LOCATOR",
                "SECRET_EXCERPT",
                "SECRET_SOURCE_TEXT",
                "SECRET_RATIONALE",
                "SECRET_ACTION",
                "SECRET_RAW_PROVIDER",
            ):
                assert sentinel not in feedback
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(response)))]
        ), "fake-model"

    _install_worker(monkeypatch, review=review, package=package, provider=provider, store=store)
    await _run_worker(review)
    assert calls == 2 and review.state == "completed"


@pytest.mark.parametrize(
    "category", ["unsupported_claim", "repetition", "clarity", "keyword_stuffing"]
)
@pytest.mark.parametrize("locator", ["/resume/content/summary", "/cover_letter/content"])
def test_v5_single_document_categories_accept_resume_or_cover_letter(
    category: str, locator: str
) -> None:
    package = _package()
    catalog, _projection = document_review._build_evidence_catalog_and_projection(package)
    provider = _provider_finding(
        category=category,
        evidence_ids=[_evidence_id(package, locator)],
    )
    result = document_review._canonicalize_provider_review(_provider_response([provider]), catalog)[
        0
    ]
    assert result["document"] in {"resume", "cover_letter"}


@pytest.mark.asyncio
async def test_v4_worker_reconstructs_canonical_findings_in_one_call(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="v4-success")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    target_id = _evidence_id(package.content, "/resume/content/summary")
    calls = 0

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {"findings": [_provider_finding(evidence_ids=[target_id])]}
                        )
                    )
                )
            ]
        ), "fake-model"

    _install_worker(monkeypatch, review=review, package=package, provider=provider, store=store)
    await _run_worker(review)
    assert calls == 1 and review.state == "completed"
    assert review.findings[0]["locator"] == "/resume/content/summary"
    assert review.diagnostic_summary is not None
    assert review.diagnostic_summary["generation_count"] == 1


@pytest.mark.asyncio
async def test_v4_worker_corrects_once_then_stops(monkeypatch: MonkeyPatch) -> None:
    package = _worker_package()
    review = _review(package, review_id="v4-gen2")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    target_id = _evidence_id(package.content, "/resume/content/summary")
    responses = [
        {"findings": [_provider_finding(evidence_ids=["resume-e9999"])]},
        {"findings": [_provider_finding(evidence_ids=[target_id])]},
    ]
    calls = 0

    async def provider(**kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        item = responses[calls]
        calls += 1
        if calls == 2:
            feedback = cast(list[dict[str, str]], kwargs["messages"])[1]["content"]
            assert "invalid_evidence_id" in feedback and "e9999" not in feedback
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(item)))]
        ), "fake-model"

    _install_worker(monkeypatch, review=review, package=package, provider=provider, store=store)
    await _run_worker(review)
    assert calls == 2 and review.state == "completed"
    assert review.diagnostic_summary is not None
    assert review.diagnostic_summary["completed_on_generation"] == 2


@pytest.mark.asyncio
@pytest.mark.skip(reason="V10 structurally blocks the provider mismatch before semantic correction")
async def test_v5_worker_regenerates_after_role_document_mismatch_without_leaking_content(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="v5-role-mismatch-success")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    requirement_id = _evidence_id(package.content, "/job/description")
    resume_id = _evidence_id(package.content, "/resume/content/summary")
    responses = [
        {
            "findings": [
                _provider_finding(
                    category="job_document_mismatch",
                    evidence_ids=[
                        _evidence_id(package.content, "/cover_letter/content"),
                        resume_id,
                    ],
                )
            ]
        },
        {
            "findings": [
                _provider_finding(
                    category="job_document_mismatch",
                    evidence_ids=[
                        requirement_id,
                        resume_id,
                    ],
                )
            ]
        },
    ]
    calls = 0

    async def provider(**kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        payload = responses[calls]
        calls += 1
        if calls == 2:
            feedback = cast(list[dict[str, str]], kwargs["messages"])[1]["content"]
            for value in ("job_document_mismatch", "job", "resume", "cover_letter"):
                assert value in feedback
            assert resume_id not in feedback and "Built APIs" not in feedback
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
        ), "fake-model"

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    assert calls == 2 and review.state == "completed"
    finding = cast(dict[str, object], cast(list[object], review.findings)[0])
    evidence = cast(list[dict[str, object]], finding["evidence"])
    assert evidence[0]["document"] == "job"
    durable = json.dumps(review.diagnostic_summary, sort_keys=True)
    for transient_key in ("category", "expected_documents", "actual_document"):
        assert transient_key not in durable
    assert "job_document_mismatch" not in str(diagnostic_logger.events)


@pytest.mark.asyncio
@pytest.mark.skip(reason="V10 structurally blocks the provider mismatch before semantic correction")
async def test_v5_worker_stops_after_second_role_document_mismatch(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="v5-role-mismatch-terminal")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    resume_id = _evidence_id(package.content, "/resume/content/summary")
    invalid = {
        "findings": [
            _provider_finding(
                category="job_document_mismatch",
                evidence_ids=[
                    _evidence_id(package.content, "/cover_letter/content"),
                    resume_id,
                ],
            )
        ]
    }
    calls = 0

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        calls += 1
        if calls > 2:
            raise AssertionError("third generation attempted")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(invalid)))]
        ), "fake-model"

    _install_worker(monkeypatch, review=review, package=package, provider=provider, store=store)
    await _run_worker(review)

    assert calls == 2 and review.state == "failed"


def _precedence_package() -> dict[str, object]:
    return {
        "job": {"title": "Engineer", "description": "Requires Python"},
        "resume": {
            "content": {"summary": "Built APIs", "nested": ["Led delivery"]},
            "provenance": {"source_revision_sha256": "a"},
        },
        "cover_letter": {"content": "Built APIs for this role", "closing": "Thank you"},
    }


def _provider_payload(category: str, evidence_ids: list[str]) -> dict[str, object]:
    return _provider_finding(category=category, evidence_ids=evidence_ids)


@pytest.mark.skip(reason="Replaced by V10 category-specific provider-schema coverage")
def test_v6_provider_schema_is_id_only_and_bounded() -> None:
    from pydantic import ValidationError

    from app.domain.applications.schemas import ProviderDocumentReviewResponseV7

    valid = {"findings": [_provider_payload("repetition", ["e0001"])]}
    assert ProviderDocumentReviewResponseV7.model_validate(valid).findings[0].evidence_ids == [
        "e0001"
    ]
    for field, value in (
        ("evidence", [{"role": "target", "evidence_id": "e0001"}]),
        ("role", "target"),
        ("document", "resume"),
        ("locator", "/resume/content"),
        ("excerpt", "Built APIs"),
    ):
        invalid = _provider_payload("repetition", ["e0001"])
        invalid[field] = value
        with pytest.raises(ValidationError):
            ProviderDocumentReviewResponseV7.model_validate({"findings": [invalid]})
    for ids in ([], [""], ["e" * 33], ["e0001", "e0002", "e0003"]):
        with pytest.raises(ValidationError):
            ProviderDocumentReviewResponseV7.model_validate(
                {"findings": [_provider_payload("repetition", ids)]}
            )


@pytest.mark.skip(reason="Replaced by V10 provider-boundary coverage")
def test_v7_requires_explicit_top_level_findings_but_allows_empty_list() -> None:
    from pydantic import ValidationError

    from app.domain.applications.schemas import ProviderDocumentReviewResponseV7

    assert ProviderDocumentReviewResponseV7.model_validate({"findings": []}).findings == []
    with pytest.raises(ValidationError):
        ProviderDocumentReviewResponseV7.model_validate({})


@pytest.mark.skip(reason="Replaced by V10 namespace normalization coverage")
def test_v7_normalizes_schema_issues_without_provider_content() -> None:
    from pydantic import ValidationError

    from app.domain.applications.schemas import ProviderDocumentReviewResponseV10

    malformed = {
        "findings": [
            {
                "id": "f1",
                "category": "INVALID_CATEGORY_SENTINEL",
                "severity": "invalid",
                "rationale": "SECRET_RATIONALE_SENTINEL",
                "evidence_ids": "SECRET_EVIDENCE_ID_SENTINEL",
                "proposed_action": "SECRET_ACTION_SENTINEL",
                "LOCATOR_SENTINEL": "SECRET_EXCERPT_SENTINEL",
            }
        ]
    }
    with pytest.raises(ValidationError) as exc_info:
        ProviderDocumentReviewResponseV10.model_validate(malformed)
    issues = document_review._normalize_schema_validation_issues(exc_info.value)
    assert {tuple(issue.items()) for issue in issues} == {
        (
            ("schema_issue_type", "extra_forbidden_field"),
            ("field_path", "findings.*.<unknown_field>"),
        ),
        (("schema_issue_type", "invalid_field_type"), ("field_path", "findings.*.evidence_ids")),
        (("schema_issue_type", "invalid_literal"), ("field_path", "findings.*.category")),
        (("schema_issue_type", "invalid_literal"), ("field_path", "findings.*.severity")),
    }
    feedback = schema_corrective_review_messages(projection=_package(), issues=issues)[1]["content"]
    for sentinel in (
        "SECRET_EVIDENCE_ID_SENTINEL",
        "SECRET_RATIONALE_SENTINEL",
        "SECRET_ACTION_SENTINEL",
        "LOCATOR_SENTINEL",
        "SECRET_EXCERPT_SENTINEL",
    ):
        assert sentinel not in feedback


@pytest.mark.skip(reason="Replaced by V10 namespace normalization coverage")
def test_v7_schema_issue_normalization_covers_bounds_shapes_deduplication_and_cap() -> None:
    from pydantic import ValidationError

    from app.domain.applications.schemas import ProviderDocumentReviewResponseV10

    malformed = {
        "findings": [
            {
                "id": "",
                "category": "repetition",
                "severity": "suggestion",
                "rationale": "x",
                "evidence_ids": ["e0001"],
                "proposed_action": "x",
            },
            {
                "id": "x" * 65,
                "category": "repetition",
                "severity": "suggestion",
                "rationale": "x",
                "evidence_ids": ["e0001"],
                "proposed_action": "x",
            },
            [],
        ]
    }
    with pytest.raises(ValidationError) as exc_info:
        ProviderDocumentReviewResponseV10.model_validate(malformed)
    issues = document_review._normalize_schema_validation_issues(exc_info.value)
    assert {tuple(issue.items()) for issue in issues} == {
        (("schema_issue_type", "too_short"), ("field_path", "findings.*.id")),
        (("schema_issue_type", "too_long"), ("field_path", "findings.*.id")),
        (("schema_issue_type", "invalid_object_shape"), ("field_path", "findings.*")),
    }

    cap_error = ValidationError.from_exception_data(
        "ProviderDocumentReviewResponseV7",
        [
            {"type": "missing", "loc": ("findings", 0, field), "input": None}
            for field in (
                "id",
                "category",
                "severity",
                "rationale",
                "evidence_ids",
                "proposed_action",
                "evidence",
                "role",
                "document",
                "locator",
            )
        ],
    )
    capped = document_review._normalize_schema_validation_issues(cap_error)
    assert len(capped) == 8
    assert capped == tuple(
        sorted(capped, key=lambda issue: (issue["schema_issue_type"], issue["field_path"]))
    )
    assert document_review._normalize_schema_validation_issues(cap_error) == capped


def _schema_failure_events(logger: _DiagnosticLogger) -> list[dict[str, object]]:
    return [
        fields
        for event, fields in logger.events
        if event == "document_review_schema_validation_failed"
    ]


def _canonical_failure_events(logger: _DiagnosticLogger) -> list[dict[str, object]]:
    return [
        fields
        for event, fields in logger.events
        if event == "document_review_canonical_validation_failed"
    ]


def test_v10_provider_rejects_legacy_evidence_at_the_provider_boundary() -> None:
    payload = _provider_finding(evidence_ids=["resume-e0001"])
    payload["evidence"] = [{"role": "target", "evidence_id": "e0001"}]

    with pytest.raises(ValidationError) as exc_info:
        ProviderDocumentReviewResponseV10.model_validate({"findings": [payload]})

    assert document_review._normalize_schema_validation_issues(exc_info.value) == (
        {"schema_issue_type": "extra_forbidden_field", "field_path": "findings.*.evidence"},
    )


@pytest.mark.asyncio
async def test_v8_worker_canonicalizes_a_full_long_source_leaf_in_one_generation(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    source_leaf = "L" * 3_001
    package.content["resume"] = {
        "content": {"summary": source_leaf},
        "provenance": {"source_revision_sha256": "a"},
    }
    encoded = json.dumps(package.content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    package.content_sha256 = hashlib.sha256(encoded.encode()).hexdigest()
    review = _review(package, review_id="v8-long-leaf")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    target_id = _evidence_id(package.content, "/resume/content/summary")
    calls = 0

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {"findings": [_provider_finding(evidence_ids=[target_id])]}
                        )
                    )
                )
            ]
        ), "fake-model"

    _install_worker(monkeypatch, review=review, package=package, provider=provider, store=store)
    await _run_worker(review)

    finding = review.findings[0]
    assert calls == 1 and review.state == "completed"
    assert finding["excerpt"] == source_leaf
    assert cast(list[dict[str, object]], finding["evidence"])[0]["excerpt"] == source_leaf
    assert _json_pointer(package.content, cast(str, finding["locator"])) == finding["excerpt"]


@pytest.mark.asyncio
async def test_v8_worker_canonicalizes_the_largest_valid_serialized_package_leaf(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    package.content["resume"] = {
        "content": {"summary": ""},
        "provenance": {"source_revision_sha256": "a"},
    }
    empty_encoded = json.dumps(
        package.content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    source_leaf = "B" * (MAX_REVIEW_INPUT_CHARS - len(empty_encoded))
    package.content["resume"] = {
        "content": {"summary": source_leaf},
        "provenance": {"source_revision_sha256": "a"},
    }
    encoded = json.dumps(package.content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert len(encoded) == MAX_REVIEW_INPUT_CHARS
    package.content_sha256 = hashlib.sha256(encoded.encode()).hexdigest()
    review = _review(package, review_id="v8-package-ceiling")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    target_id = _evidence_id(package.content, "/resume/content/summary")

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {"findings": [_provider_finding(evidence_ids=[target_id])]}
                        )
                    )
                )
            ]
        ), "fake-model"

    _install_worker(monkeypatch, review=review, package=package, provider=provider, store=store)
    await _run_worker(review)
    assert review.state == "completed"
    assert review.findings[0]["excerpt"] == source_leaf


class _ForcedCanonicalFinding(BaseModel):
    model_config = ConfigDict(extra="ignore")
    excerpt: str = Field(max_length=1)


@pytest.mark.asyncio
async def test_v8_canonical_pydantic_failure_is_terminal_and_content_safe(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    source = "SECRET_SOURCE_SENTINEL"
    package.content["resume"] = {
        "content": {"summary": source},
        "provenance": {"source_revision_sha256": "a"},
    }
    encoded = json.dumps(package.content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    package.content_sha256 = hashlib.sha256(encoded.encode()).hexdigest()
    review = _review(package, review_id="v8-canonical-gen1")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    target_id = _evidence_id(package.content, "/resume/content/summary")
    calls = 0

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "findings": [
                                    {
                                        **_provider_finding(evidence_ids=[target_id]),
                                        "rationale": "SECRET_RATIONALE_SENTINEL",
                                        "proposed_action": "SECRET_ACTION_SENTINEL",
                                    }
                                ]
                            }
                        )
                    )
                )
            ]
        ), "fake-model"

    monkeypatch.setattr(document_review, "DocumentReviewFinding", _ForcedCanonicalFinding)
    logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    assert calls == 1 and review.state == "failed"
    assert _schema_failure_events(logger) == []
    assert _canonical_failure_events(logger) == [
        {"generation": 1, "exception_class": "ValidationError", "validation_error_count": 1}
    ]
    assert review.diagnostic_summary is not None
    assert review.diagnostic_summary["generations"] == [
        {"generation_attempt": 1, "stage": "canonical_validation"}
    ]
    rendered = json.dumps((logger.events, review.diagnostic_summary), sort_keys=True)
    for sentinel in (
        source,
        target_id,
        "SECRET_RATIONALE_SENTINEL",
        "SECRET_ACTION_SENTINEL",
        "/resume/content/summary",
    ):
        assert sentinel not in rendered


@pytest.mark.asyncio
async def test_v8_canonical_failure_after_eligible_evidence_correction_never_calls_gen3(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="v8-canonical-gen2")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    target_id = _evidence_id(package.content, "/resume/content/summary")
    responses = [
        {"findings": [_provider_finding(evidence_ids=["resume-e9999"])]},
        {"findings": [_provider_finding(evidence_ids=[target_id])]},
    ]
    calls = 0

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        if calls >= 2:
            raise AssertionError("third generation attempted")
        payload = responses[calls]
        calls += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
        ), "fake-model"

    monkeypatch.setattr(document_review, "DocumentReviewFinding", _ForcedCanonicalFinding)
    logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)
    assert calls == 2 and review.state == "failed"
    assert _schema_failure_events(logger) == []
    assert _canonical_failure_events(logger) == [
        {"generation": 2, "exception_class": "ValidationError", "validation_error_count": 1}
    ]


@pytest.mark.asyncio
async def test_v8_duplicate_finding_id_is_a_terminal_non_pydantic_canonical_failure(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="v8-canonical-invariant")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    target_id = _evidence_id(package.content, "/resume/content/summary")
    finding = _provider_finding(evidence_ids=[target_id])

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps({"findings": [finding, finding]}))
                )
            ]
        ), "fake-model"

    logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)
    assert review.state == "failed"
    assert _schema_failure_events(logger) == []
    assert _canonical_failure_events(logger) == [
        {"generation": 1, "exception_class": "_SchemaValidationError", "validation_error_count": 0}
    ]


@pytest.mark.asyncio
async def test_v8_worker_logs_one_safe_gen1_schema_failure_event(monkeypatch: MonkeyPatch) -> None:
    from pydantic import ValidationError

    from app.domain.applications.schemas import ProviderDocumentReviewResponseV10

    package = _worker_package()
    review = _review(package, review_id="v7-schema-log-gen1")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    target_id = _evidence_id(package.content, "/resume/content/summary")
    malformed = {
        "findings": [
            {
                "id": "",
                "category": "INVALID_CATEGORY_SENTINEL",
                "severity": "invalid",
                "rationale": "SECRET_RATIONALE_SENTINEL",
                "evidence_ids": ["SECRET_EVIDENCE_ID_SENTINEL"],
                "proposed_action": "",
                "secret_customer_value_ABC123": "SECRET_SOURCE_TEXT_SENTINEL",
            }
        ]
    }
    with pytest.raises(ValidationError) as exc_info:
        ProviderDocumentReviewResponseV10.model_validate(malformed)
    expected_issues = document_review._normalize_schema_validation_issues(exc_info.value)
    expected_count = exc_info.value.error_count()
    responses = [malformed, {"findings": [_provider_finding(evidence_ids=[target_id])]}]

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        payload = responses.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
        ), "fake-model"

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    events = _schema_failure_events(diagnostic_logger)
    assert len(events) == 1
    assert _canonical_failure_events(diagnostic_logger) == []
    assert events[0] == {
        "generation": 1,
        "validation_error_count": expected_count,
        "normalized_schema_issues": expected_issues,
    }
    rendered = json.dumps(events, sort_keys=True)
    for sentinel in (
        "SECRET_EVIDENCE_ID_SENTINEL",
        "SECRET_RATIONALE_SENTINEL",
        "SECRET_SOURCE_TEXT_SENTINEL",
        "INVALID_CATEGORY_SENTINEL",
        "secret_customer_value_ABC123",
    ):
        assert sentinel not in rendered
    assert "findings.*" in rendered
    assert review.state == "completed"


@pytest.mark.asyncio
async def test_v7_worker_logs_safe_schema_events_for_both_generations(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="v7-schema-log-gen2")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    calls = 0

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        calls += 1
        if calls > 2:
            raise AssertionError("third generation attempted")
        payload: dict[str, object] = {"findings": [{"id": ""}]} if calls == 1 else {}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
        ), "fake-model"

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    events = _schema_failure_events(diagnostic_logger)
    assert calls == 2
    assert [event["generation"] for event in events] == [1, 2]
    assert all(
        set(event) == {"generation", "validation_error_count", "normalized_schema_issues"}
        for event in events
    )
    assert all(isinstance(event["validation_error_count"], int) for event in events)
    assert review.state == "failed"
    rendered_diagnostic = json.dumps(review.diagnostic_summary, sort_keys=True)
    assert "document-review-diagnostic-v1" in rendered_diagnostic
    assert "schema_issue_type" not in rendered_diagnostic
    assert "field_path" not in rendered_diagnostic
    assert "normalized_schema_issues" not in rendered_diagnostic


@pytest.mark.asyncio
async def test_v7_schema_failure_event_uses_deduplicated_sorted_capped_issues(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="v7-schema-log-normalized")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    valid = _provider_finding(
        evidence_ids=[_evidence_id(package.content, "/resume/content/summary")]
    )
    malformed = {
        "findings": [
            {},
            {},
            {**valid, "id": ""},
            {**valid, "id": "x" * 65},
            {**valid, "id": 1},
            {**valid, "secret_customer_value_ABC123": "SECRET_EXTRA_VALUE_SENTINEL"},
        ]
    }
    responses = [malformed, {"findings": [valid]}]

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        payload = responses.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
        ), "fake-model"

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    events = _schema_failure_events(diagnostic_logger)
    assert len(events) == 1
    issues = cast(tuple[dict[str, str], ...], events[0]["normalized_schema_issues"])
    assert 1 <= len(issues) <= 8
    assert issues == tuple(
        sorted(issues, key=lambda issue: (issue["schema_issue_type"], issue["field_path"]))
    )
    assert len({(issue["schema_issue_type"], issue["field_path"]) for issue in issues}) == len(
        issues
    )
    assert "findings.*.<unknown_field>" in {issue["field_path"] for issue in issues}
    assert "secret_customer_value_ABC123" not in json.dumps(events, sort_keys=True)


def test_v7_public_response_schema_excludes_observability_metadata() -> None:
    assert {"schema_issue_type", "field_path", "normalized_schema_issues"}.isdisjoint(
        DocumentReviewResponse.model_fields
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first", "feedback_text"),
    [
        ({}, "missing_required_field: findings"),
        (
            {
                "findings": [
                    {
                        "id": "f1",
                        "category": "repetition",
                        "severity": "suggestion",
                        "rationale": "x",
                        "evidence": [{"evidence_id": "SECRET_EVIDENCE_ID_SENTINEL"}],
                        "proposed_action": "x",
                    }
                ]
            },
            "extra_forbidden_field: findings.*.evidence",
        ),
        (
            {
                "findings": [
                    {
                        "id": "f1",
                        "category": "repetition",
                        "severity": "suggestion",
                        "rationale": "x",
                        "proposed_action": "x",
                    }
                ]
            },
            "missing_required_field: findings.*.target_evidence_id",
        ),
        (
            {
                "findings": [
                    {
                        **_provider_finding(evidence_ids=["resume-e0001"]),
                        "role": "target",
                        "document": "resume",
                    }
                ]
            },
            "extra_forbidden_field: findings.*.document",
        ),
        (
            {
                "findings": [
                    {**_provider_finding(evidence_ids=["resume-e0001"]), "category": "invalid"}
                ]
            },
            "invalid_field_type: findings.*",
        ),
        (
            {
                "findings": [
                    {**_provider_finding(evidence_ids=["resume-e0001"]), "evidence_ids": "wrong"}
                ]
            },
            "extra_forbidden_field: findings.*.evidence_ids",
        ),
    ],
)
async def test_v8_worker_corrects_schema_once_then_completes(
    monkeypatch: MonkeyPatch,
    first: dict[str, object],
    feedback_text: str,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="v7-schema-correction")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    target_id = _evidence_id(package.content, "/resume/content/summary")
    responses = [first, {"findings": [_provider_finding(evidence_ids=[target_id])]}]
    calls = 0

    async def provider(**kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        payload = responses[calls]
        calls += 1
        if calls == 2:
            assert feedback_text in cast(list[dict[str, str]], kwargs["messages"])[1]["content"]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
        ), "fake-model"

    _install_worker(monkeypatch, review=review, package=package, provider=provider, store=store)
    await _run_worker(review)
    assert calls == 2 and review.state == "completed"


@pytest.mark.asyncio
async def test_v10_worker_corrects_invalid_evidence_id_namespace_then_completes(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="v10-namespace-schema-correction")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    resume_id = _evidence_id(package.content, "/resume/content/summary")
    cover_letter_id = _evidence_id(package.content, "/cover_letter/content")
    invalid = {
        "id": "namespace-correction",
        "category": "cross_document_mismatch",
        "severity": "suggestion",
        "rationale": "Documents conflict",
        "resume_evidence_id": resume_id,
        "cover_letter_evidence_id": resume_id,
        "proposed_action": "Align the documents",
    }
    valid = {
        **invalid,
        "cover_letter_evidence_id": cover_letter_id,
    }
    calls: list[dict[str, object]] = []

    async def provider(**kwargs: object) -> tuple[Any, str]:
        calls.append(kwargs)
        if len(calls) > 2:
            raise AssertionError("third generation attempted")
        if len(calls) == 2:
            feedback = cast(list[dict[str, str]], kwargs["messages"])[1]["content"]
            assert "previous JSON did not match the required response schema" in feedback
            assert "invalid_evidence_id_namespace: findings.*.cover_letter_evidence_id" in feedback
            for forbidden in (
                resume_id,
                "Documents conflict",
                "Align the documents",
                "^",
                "regex",
                "pattern",
                "input",
            ):
                assert forbidden not in feedback
        payload = invalid if len(calls) == 1 else valid
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps({"findings": [payload]}))
                )
            ]
        ), "fake-model"

    diagnostic_logger = _install_worker(
        monkeypatch, review=review, package=package, provider=provider, store=store
    )
    await _run_worker(review)

    schema_events = _schema_failure_events(diagnostic_logger)
    assert len(calls) == 2
    assert review.state == "completed"
    assert store.data[review.task_id]["status"] == "done"
    assert review.diagnostic_summary == {
        "diagnostic_version": "document-review-diagnostic-v1",
        "review_id": review.id,
        "task_id": review.task_id,
        "outcome": "completed",
        "generation_count": 2,
        "completed_on_generation": 2,
    }
    assert schema_events == [
        {
            "generation": 1,
            "validation_error_count": 1,
            "normalized_schema_issues": (
                {
                    "schema_issue_type": "invalid_evidence_id_namespace",
                    "field_path": "findings.*.cover_letter_evidence_id",
                },
            ),
        }
    ]
    assert _canonical_failure_events(diagnostic_logger) == []
    assert review.findings[0]["document"] == "resume"
    assert review.findings[0]["locator"] == "/resume/content/summary"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "second",
    [
        {},
        {"findings": [_provider_finding(evidence_ids=["resume-e9999"])]},
    ],
)
async def test_v7_schema_first_never_permits_a_third_generation(
    monkeypatch: MonkeyPatch,
    second: dict[str, object],
) -> None:
    package = _worker_package()
    review = _review(package, review_id="v7-global-cap")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    calls = 0

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        if calls >= 2:
            raise AssertionError("third generation attempted")
        payload = [{}, second][calls]
        calls += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
        ), "fake-model"

    _install_worker(monkeypatch, review=review, package=package, provider=provider, store=store)
    await _run_worker(review)
    assert calls == 2 and review.state == "failed"


@pytest.mark.asyncio
async def test_v7_evidence_first_schema_second_never_permits_a_third_generation(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="v7-evidence-schema-cap")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    calls = 0

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        if calls >= 2:
            raise AssertionError("third generation attempted")
        payload = [{"findings": [_provider_finding(evidence_ids=["resume-e9999"])]}, {}][calls]
        calls += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
        ), "fake-model"

    _install_worker(monkeypatch, review=review, package=package, provider=provider, store=store)
    await _run_worker(review)
    assert calls == 2 and review.state == "failed"


@pytest.mark.parametrize(
    ("category", "locators", "expected_roles", "target_locator"),
    [
        ("unsupported_claim", ["/resume/content/summary"], ["target"], "/resume/content/summary"),
        ("repetition", ["/cover_letter/content"], ["target"], "/cover_letter/content"),
        ("clarity", ["/resume/content/summary"], ["target"], "/resume/content/summary"),
        ("keyword_stuffing", ["/cover_letter/content"], ["target"], "/cover_letter/content"),
        (
            "cross_document_mismatch",
            ["/resume/content/summary", "/cover_letter/content"],
            ["comparison", "target"],
            "/resume/content/summary",
        ),
        (
            "job_document_mismatch",
            ["/job/description", "/resume/content/summary"],
            ["requirement", "target"],
            "/resume/content/summary",
        ),
        (
            "job_document_mismatch",
            ["/job/description", "/cover_letter/content"],
            ["requirement", "target"],
            "/cover_letter/content",
        ),
        (
            "omitted_verified_evidence",
            ["/job/description", "/resume/content/summary"],
            ["requirement", "target"],
            "/resume/content/summary",
        ),
        (
            "omitted_verified_evidence",
            ["/job/description", "/cover_letter/content"],
            ["requirement", "target"],
            "/cover_letter/content",
        ),
    ],
)
def test_v6_category_contract_is_server_derived_and_test_local(
    category: str,
    locators: list[str],
    expected_roles: list[str],
    target_locator: str,
) -> None:
    package = _precedence_package()
    catalog, _projection = document_review._build_evidence_catalog_and_projection(package)
    result = document_review._canonicalize_provider_review(
        _provider_response(
            [_provider_payload(category, [_evidence_id(package, locator) for locator in locators])]
        ),
        catalog,
    )[0]
    evidence = cast(list[dict[str, object]], result["evidence"])
    assert [item["role"] for item in evidence] == expected_roles
    assert result["locator"] == target_locator
    assert result["excerpt"] == catalog[_evidence_id(package, target_locator)].text


@pytest.mark.parametrize(
    ("category", "locators", "reason"),
    [
        (
            "job_document_mismatch",
            ["/resume/content/summary", "/cover_letter/content"],
            "duplicate_evidence_role",
        ),
        (
            "job_document_mismatch",
            ["/resume/content/summary", "/resume/content/nested/0"],
            "duplicate_evidence_role",
        ),
        ("job_document_mismatch", ["/job/title", "/job/description"], "duplicate_evidence_role"),
        ("job_document_mismatch", ["/resume/content/summary"], "missing_required_evidence"),
        (
            "cross_document_mismatch",
            ["/resume/content/summary", "/resume/content/nested/0"],
            "duplicate_evidence_role",
        ),
        (
            "cross_document_mismatch",
            ["/cover_letter/content", "/cover_letter/closing"],
            "duplicate_evidence_role",
        ),
        (
            "cross_document_mismatch",
            ["/job/description", "/resume/content/summary"],
            "invalid_evidence_document",
        ),
        ("cross_document_mismatch", ["/resume/content/summary"], "missing_required_evidence"),
        ("repetition", ["/job/description"], "invalid_evidence_document"),
        (
            "repetition",
            ["/resume/content/summary", "/cover_letter/content"],
            "duplicate_evidence_role",
        ),
    ],
)
@pytest.mark.skip(
    reason="V10 structurally rejects invalid slot compositions before semantic validation"
)
def test_v6_failure_precedence_is_explicit_and_independent(
    category: str,
    locators: list[str],
    reason: str,
) -> None:
    package = _precedence_package()
    catalog, _projection = document_review._build_evidence_catalog_and_projection(package)
    with pytest.raises(document_review._EvidenceValidationError, match=reason):
        document_review._canonicalize_provider_review(
            _provider_response(
                [
                    _provider_payload(
                        category, [_evidence_id(package, locator) for locator in locators]
                    )
                ]
            ),
            catalog,
        )


@pytest.mark.skip(reason="V10 slots make duplicate provider selections structurally impossible")
def test_v6_duplicate_id_precedes_document_and_role_validation() -> None:
    package = _precedence_package()
    catalog, _projection = document_review._build_evidence_catalog_and_projection(package)
    repeated = _evidence_id(package, "/job/description")
    with pytest.raises(document_review._EvidenceValidationError, match="duplicate_evidence_id"):
        document_review._canonicalize_provider_review(
            _provider_response([_provider_payload("repetition", [repeated, repeated])]),
            catalog,
        )


def test_v6_job_target_derivation_is_order_independent() -> None:
    package = _precedence_package()
    catalog, _projection = document_review._build_evidence_catalog_and_projection(package)
    ids = [
        _evidence_id(package, "/job/description"),
        _evidence_id(package, "/resume/content/summary"),
    ]
    first = document_review._canonicalize_provider_review(
        _provider_response([_provider_payload("job_document_mismatch", ids)]), catalog
    )
    second = document_review._canonicalize_provider_review(
        _provider_response([_provider_payload("job_document_mismatch", list(reversed(ids)))]),
        catalog,
    )
    assert first == second


@pytest.mark.parametrize(
    ("payload", "path"),
    [
        (
            {
                "category": "cross_document_mismatch",
                "resume_evidence_id": "resume-e0001",
                "cover_letter_evidence_id": "resume-e0001",
            },
            "findings.*.cover_letter_evidence_id",
        ),
        (
            {
                "category": "job_document_mismatch",
                "job_evidence_id": "resume-e0001",
                "target_evidence_id": "resume-e0001",
            },
            "findings.*.job_evidence_id",
        ),
        (
            {
                "category": "job_document_mismatch",
                "job_evidence_id": "job-e0001",
                "target_evidence_id": "job-e0001",
            },
            "findings.*.target_evidence_id",
        ),
        (
            {"category": "clarity", "target_evidence_id": "job-e0001"},
            "findings.*.target_evidence_id",
        ),
    ],
)
def test_v10_provider_schema_structurally_rejects_wrong_document_slots(
    payload: dict[str, str], path: str
) -> None:
    finding = {
        "id": "f1",
        "severity": "suggestion",
        "rationale": "x",
        "proposed_action": "x",
        **payload,
    }
    with pytest.raises(ValidationError) as exc_info:
        ProviderDocumentReviewResponseV10.model_validate({"findings": [finding]})
    issues = document_review._normalize_schema_validation_issues(exc_info.value)
    assert {tuple(issue.items()) for issue in issues} == {
        (("schema_issue_type", "invalid_evidence_id_namespace"), ("field_path", path)),
    }
    rendered = json.dumps(issues)
    assert all(
        value not in rendered for value in ("resume-e0001", "job-e0001", "^", "pattern", "input")
    )


def test_v10_provider_schema_rejects_legacy_evidence_ids_without_conversion() -> None:
    payload = {
        "id": "f1",
        "category": "clarity",
        "severity": "suggestion",
        "rationale": "x",
        "evidence_ids": ["resume-e0001"],
        "proposed_action": "x",
    }
    with pytest.raises(ValidationError) as exc_info:
        ProviderDocumentReviewResponseV10.model_validate({"findings": [payload]})
    issues = document_review._normalize_schema_validation_issues(exc_info.value)
    assert {tuple(issue.items()) for issue in issues} == {
        (("schema_issue_type", "extra_forbidden_field"), ("field_path", "findings.*.evidence_ids")),
        (
            ("schema_issue_type", "missing_required_field"),
            ("field_path", "findings.*.target_evidence_id"),
        ),
    }


@pytest.mark.parametrize(
    ("category", "locators", "expected_document"),
    [
        ("clarity", ["/resume/content/summary"], "resume"),
        ("clarity", ["/cover_letter/content"], "cover_letter"),
        ("cross_document_mismatch", ["/resume/content/summary", "/cover_letter/content"], "resume"),
        ("job_document_mismatch", ["/job/description", "/resume/content/summary"], "resume"),
        (
            "omitted_verified_evidence",
            ["/job/description", "/cover_letter/content"],
            "cover_letter",
        ),
    ],
)
def test_v10_canonicalizes_valid_document_slot_compositions(
    category: str,
    locators: list[str],
    expected_document: str,
) -> None:
    package = _precedence_package()
    catalog, _projection = document_review._build_evidence_catalog_and_projection(package)
    finding = _provider_finding(
        category=category, evidence_ids=[_evidence_id(package, locator) for locator in locators]
    )
    result = document_review._canonicalize_provider_review(_provider_response([finding]), catalog)[
        0
    ]
    assert result["document"] == expected_document
    assert all(
        key not in json.dumps(result)
        for key in ("job-e", "resume-e", "cover-e", "target_evidence_id", "job_evidence_id")
    )


def test_v10_unknown_qualified_id_reaches_catalog_validation() -> None:
    catalog, _projection = document_review._build_evidence_catalog_and_projection(_package())
    finding = _provider_finding(evidence_ids=["resume-e999999"])
    with pytest.raises(document_review._EvidenceValidationError, match="invalid_evidence_id"):
        document_review._canonicalize_provider_review(_provider_response([finding]), catalog)


@pytest.mark.asyncio
async def test_v10_schema_namespace_failure_corrects_once_then_stops(
    monkeypatch: MonkeyPatch,
) -> None:
    package = _worker_package()
    review = _review(package, review_id="v10-namespace-cap")
    store = _MemoryTaskStore()
    store.data[review.task_id] = {"user_id": 1, "status": "queued"}
    invalid = {
        "findings": [
            {
                "id": "f1",
                "category": "clarity",
                "severity": "suggestion",
                "rationale": "x",
                "target_evidence_id": "job-e0001",
                "proposed_action": "x",
            }
        ]
    }
    calls = 0

    async def provider(**_kwargs: object) -> tuple[Any, str]:
        nonlocal calls
        calls += 1
        if calls > 2:
            raise AssertionError("third generation attempted")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(invalid)))]
        ), "fake-model"

    _install_worker(monkeypatch, review=review, package=package, provider=provider, store=store)
    await _run_worker(review)
    assert calls == 2 and review.state == "failed"
