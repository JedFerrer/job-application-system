"""Unit tests for interview prep schemas + LLM output parsing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.interview.exceptions import InterviewGenerationError
from app.domain.interview.schemas import InterviewPrepRequest, InterviewQuestion
from app.domain.interview.service import InterviewService


def _question(category: str = "technical") -> dict[str, str]:
    return {
        "question": "Explain async I/O in Python.",
        "category": category,
        "why_it_matters": "Backend roles lean on concurrency.",
        "suggested_angle": "Reference asyncio event loop experience.",
    }


def test_request_clamps_question_count_bounds() -> None:
    with pytest.raises(ValidationError):
        InterviewPrepRequest.model_validate({"question_count": 2})
    with pytest.raises(ValidationError):
        InterviewPrepRequest.model_validate({"question_count": 21})
    assert InterviewPrepRequest().question_count == 8


def test_question_rejects_unknown_category() -> None:
    with pytest.raises(ValidationError):
        InterviewQuestion.model_validate(_question(category="trivia"))


def test_parse_questions_handles_code_fences() -> None:
    content = '```json\n{"questions": [' + _json(_question()) + "]}\n```"
    parsed = InterviewService._parse_questions(content, expected_count=8)
    assert len(parsed) == 1
    assert parsed[0].category == "technical"


def test_parse_questions_skips_malformed_entries() -> None:
    content = (
        '{"questions": ['
        + _json(_question())
        + ', {"question": "no fields"}]}'
    )
    parsed = InterviewService._parse_questions(content, expected_count=8)
    assert len(parsed) == 1  # malformed second entry dropped


def test_parse_questions_truncates_to_expected_count() -> None:
    items = ", ".join(_json(_question()) for _ in range(5))
    content = '{"questions": [' + items + "]}"
    parsed = InterviewService._parse_questions(content, expected_count=3)
    assert len(parsed) == 3


def test_parse_questions_raises_on_non_json() -> None:
    with pytest.raises(InterviewGenerationError):
        InterviewService._parse_questions("not json at all", expected_count=8)


def test_parse_questions_raises_when_all_invalid() -> None:
    content = '{"questions": [{"question": "incomplete"}]}'
    with pytest.raises(InterviewGenerationError):
        InterviewService._parse_questions(content, expected_count=8)


def _json(obj: dict[str, str]) -> str:
    import json

    return json.dumps(obj)
