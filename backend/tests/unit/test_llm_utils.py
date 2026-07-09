"""Unit tests for LLM utility helpers."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from app.infrastructure.llm_utils import strip_code_fences


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        ("plain text", "plain text"),
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('```\n{"a": 1}\n```', '{"a": 1}'),
        ('```json {"a": 1}```', '{"a": 1}'),
        ('  ```json\n{"a": 1}', '{"a": 1}'),  # missing closing fence
    ],
)
def test_strip_code_fences(raw: str, expected: str) -> None:
    assert strip_code_fences(raw) == expected
