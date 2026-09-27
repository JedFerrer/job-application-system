"""Pure Unit 2A contract tests that do not require a database."""

# ruff: noqa: E501

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.domain.applications.exceptions import PreparationSelectionInvalidError
from app.domain.applications.preparation import (
    ApplicationPreparationService,
    _canonical_hash,
    _questions_for_storage,
)
from app.domain.applications.schemas import ManualQuestion, PreparationPatch
from app.models.application import Application
from app.models.user_job_match import UserJobMatch

pytestmark = pytest.mark.unit


def test_selected_cover_letter_requires_id() -> None:
    with pytest.raises(ValidationError):
        PreparationPatch(expected_version=0, cover_letter_mode="selected")


def test_not_needed_cannot_carry_letter_id() -> None:
    with pytest.raises(ValidationError):
        PreparationPatch(
            expected_version=0,
            cover_letter_mode="not_needed",
            cover_letter_id=7,
        )


def test_manual_question_edit_resets_acknowledgement() -> None:
    question_id = "00000000-0000-0000-0000-000000000001"
    previous = [{"id": question_id, "question": "Question", "answer": "Old", "acknowledged": True}]
    result = _questions_for_storage(
        [ManualQuestion(id=question_id, question="Question", answer="New", acknowledged=True)],
        previous,
    )
    assert result[0]["acknowledged"] is False


def test_manual_question_utf8_limit_is_bytes_not_characters() -> None:
    questions = [
        ManualQuestion(question="q", answer="😀" * 4_000, acknowledged=False) for _ in range(5)
    ]
    with pytest.raises(PreparationSelectionInvalidError):
        _questions_for_storage(questions, [])


@pytest.mark.parametrize("count,valid", [(20, True), (21, False)])
def test_manual_question_count_boundary(count: int, valid: bool) -> None:
    questions = [ManualQuestion(question="q", answer="a") for _ in range(count)]
    if valid:
        assert (
            PreparationPatch(expected_version=0, manual_questions=questions).manual_questions
            == questions
        )
    else:
        with pytest.raises(ValidationError):
            PreparationPatch(expected_version=0, manual_questions=questions)


@pytest.mark.parametrize(
    ("field", "length", "valid"),
    [
        ("question", 500, True),
        ("question", 501, False),
        ("answer", 4_000, True),
        ("answer", 4_001, False),
    ],
)
def test_manual_question_field_boundaries(field: str, length: int, valid: bool) -> None:
    values = {"question": "q", "answer": "a"}
    values[field] = "x" * length
    if valid:
        assert ManualQuestion(**values)
    else:
        with pytest.raises(ValidationError):
            ManualQuestion(**values)


def test_manual_question_payload_accepts_exact_utf8_boundary_and_rejects_one_byte_over() -> None:
    questions = [
        ManualQuestion(
            id=f"00000000-0000-0000-0000-{index:012d}",
            question="q",
            answer="😀" if index == 1 else "",
            acknowledged=False,
        )
        for index in range(1, 21)
    ]

    def payload_size(values: list[ManualQuestion]) -> int:
        payload = [question.model_dump(mode="json") for question in values]
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    # Spread ASCII fill across valid 4,000-character answers, retaining a
    # multibyte value so this proves an exact serialized-byte boundary.
    remaining = 64 * 1024 - payload_size(questions)
    for question in questions:
        if remaining == 0:
            break
        available = 4_000 - len(question.answer)
        add = min(available, remaining)
        question.answer += "x" * add
        remaining -= add
    assert payload_size(questions) == 64 * 1024
    assert _questions_for_storage(questions, [])
    rejected_question = ManualQuestion(
        id=questions[-1].id,
        question="q",
        answer=questions[-1].answer + "x",
        acknowledged=False,
    )
    with pytest.raises(PreparationSelectionInvalidError):
        _questions_for_storage([*questions[:-1], rejected_question], [])


def test_manual_question_order_is_preserved() -> None:
    questions = [
        ManualQuestion(id="00000000-0000-0000-0000-000000000002", question="Second", answer="b"),
        ManualQuestion(id="00000000-0000-0000-0000-000000000001", question="First", answer="a"),
    ]
    assert [item["question"] for item in _questions_for_storage(questions, [])] == [
        "Second",
        "First",
    ]


def test_canonical_package_hash_ignores_dictionary_key_order() -> None:
    first = {
        "schema_version": "application-package-v1",
        "job": {"title": "Engineer", "company": "Acme"},
    }
    second = {
        "job": {"company": "Acme", "title": "Engineer"},
        "schema_version": "application-package-v1",
    }
    assert _canonical_hash(first) == _canonical_hash(second)


def test_canonical_package_hash_changes_for_material_content() -> None:
    first = {"resume": {"content": {"summary": "A"}}}
    second = {"resume": {"content": {"summary": "B"}}}
    assert _canonical_hash(first) != _canonical_hash(second)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reordered",
    ["matched", "missing", "nested_matched", "nested_missing", "all"],
)
async def test_fit_hash_normalizes_only_unordered_match_skill_collections(reordered: str) -> None:
    job = SimpleNamespace(
        id=1,
        title="Engineer",
        company="Acme",
        location="Remote",
        job_type="full_time",
        source="fixture",
        source_url="https://example.test/jobs/1",
        description="Build APIs",
    )
    first = UserJobMatch(
        user_id=1,
        job_id=1,
        vector_score=0.7,
        confidence="strong",
        matched_skills=["Python", "FastAPI", "React"],
        missing_keywords=["Docker", "Kubernetes"],
        deterministic_match={
            "matched_skills": ["Python", "FastAPI", "React"],
            "missing_skills": ["Docker", "Kubernetes"],
            "evidence": {"Python": "resume"},
        },
    )
    second = UserJobMatch(
        user_id=1,
        job_id=1,
        vector_score=0.7,
        confidence="strong",
        matched_skills=list(first.matched_skills or []),
        missing_keywords=list(first.missing_keywords or []),
        deterministic_match=dict(first.deterministic_match or {}),
    )
    if reordered in {"matched", "all"}:
        assert second.matched_skills is not None
        second.matched_skills.reverse()
    if reordered in {"missing", "all"}:
        assert second.missing_keywords is not None
        second.missing_keywords.reverse()
    nested = dict(second.deterministic_match or {})
    if reordered in {"nested_matched", "all"}:
        nested["matched_skills"] = list(reversed(cast(list[str], nested["matched_skills"])))
    if reordered in {"nested_missing", "all"}:
        nested["missing_skills"] = list(reversed(cast(list[str], nested["missing_skills"])))
    second.deterministic_match = nested

    async def fit_hash(match: UserJobMatch | None) -> str:
        session = SimpleNamespace(scalar=AsyncMock(return_value=match))
        service = ApplicationPreparationService(session=session)  # type: ignore[arg-type]
        service._jobs.get_by_id = AsyncMock(return_value=job)  # type: ignore[method-assign]
        application = cast(Application, SimpleNamespace(id=1, job_id=1))
        return (await service._fit_hash(application, 1))[0]

    assert await fit_hash(first) == await fit_hash(second)


@pytest.mark.asyncio
async def test_fit_hash_changes_for_material_skill_changes_and_unavailable_is_stable() -> None:
    job = SimpleNamespace(
        id=1,
        title="Engineer",
        company="Acme",
        location="Remote",
        job_type=None,
        source="fixture",
        source_url="https://example.test/jobs/1",
        description="Build APIs",
    )

    async def fit_hash(match: UserJobMatch | None) -> str:
        session = SimpleNamespace(scalar=AsyncMock(return_value=match))
        service = ApplicationPreparationService(session=session)  # type: ignore[arg-type]
        service._jobs.get_by_id = AsyncMock(return_value=job)  # type: ignore[method-assign]
        application = cast(Application, SimpleNamespace(id=1, job_id=1))
        return (await service._fit_hash(application, 1))[0]

    first = UserJobMatch(
        user_id=1,
        job_id=1,
        vector_score=0.7,
        matched_skills=["Python"],
        missing_keywords=["Docker"],
    )
    changed_skill = UserJobMatch(
        user_id=1, job_id=1, vector_score=0.7, matched_skills=["Go"], missing_keywords=["Docker"]
    )
    changed_missing = UserJobMatch(
        user_id=1,
        job_id=1,
        vector_score=0.7,
        matched_skills=["Python"],
        missing_keywords=["Kubernetes"],
    )
    assert await fit_hash(first) != await fit_hash(changed_skill)
    assert await fit_hash(first) != await fit_hash(changed_missing)
    assert await fit_hash(None) == await fit_hash(None)
