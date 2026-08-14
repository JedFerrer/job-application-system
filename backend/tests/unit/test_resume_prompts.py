import re

import pytest

from app.domain.resume.prompts import RESUME_EXTRACTION_PROMPT
from app.infrastructure.resume_parser import _source_contract_retry_prompt

pytestmark = pytest.mark.unit


_CONTAINER_NULL_PATTERNS = (
    r"\b(?:links|projects|technologies|achievements)\b.{0,80}\b(?:may|can|could|should)\s+(?:be\s+)?null\b",
    r"\b(?:links|projects|technologies|achievements)\b.{0,80}\b(?:may|can|could|should)\s+use\s+null\b",
    r"\b(?:links|projects|technologies|achievements)\b.{0,80}\b(?:are|is)\s+(?:allowed|permitted)\s+to\s+be\s+null\b",
    r"\b(?:return|use)\s+null\s+(?:for|as)\s+(?:missing\s+)?(?:links|projects|technologies|achievements)\b",
    r"\b(?:unknown|missing)\s+(?:list|object|container|fields?)\b.{0,80}\b(?:may|can|could|should)\s+(?:be\s+)?null\b",
)
_ALLOWED_SCALAR_NULL_PATTERNS = (
    r"\b(?:nullable\s+)?scalar\s+link\s+fields?\s+may\s+be\s+null\b",
    r"\b(?:nullable\s+)?scalar\s+project\s+fields?\s+may\s+be\s+null\b",
)


def _assert_no_container_null_guidance(prompt: str) -> None:
    for clause in re.split(r"[.!?\n]+", prompt):
        for pattern in _ALLOWED_SCALAR_NULL_PATTERNS:
            clause = re.sub(pattern, "", clause, flags=re.IGNORECASE)
        for pattern in _CONTAINER_NULL_PATTERNS:
            assert re.search(pattern, clause) is None, pattern


def test_extraction_prompt_uses_schema_compatible_empty_defaults() -> None:
    prompt = RESUME_EXTRACTION_PROMPT.format(resume_text="").lower()
    links_section = prompt.split("- links ", 1)[1].split("- projects ", 1)[0]
    projects_section = prompt.split("- projects ", 1)[1].split("- work_history ", 1)[0]

    assert "empty object {}" in links_section
    assert "never return\n  null or an array for links" in links_section
    assert "nullable scalar link fields may be null" in links_section

    assert "if no projects\n  are present, return []." in projects_section
    assert (
        "for each project, use [] (never null) for missing\n  technologies or achievements"
    ) in projects_section
    assert "nullable scalar project fields may be null" in projects_section

    _assert_no_container_null_guidance(prompt)
    assert "never use null for a list or object\ncontainer" in prompt
    assert "never invent data" in prompt
    assert "never infer fluency" in prompt
    assert "never translate" in prompt and "canonicalize names" in prompt


def test_extraction_prompt_can_be_formatted_with_resume_text() -> None:
    rendered = RESUME_EXTRACTION_PROMPT.format(resume_text="Candidate resume text")

    assert "Candidate resume text" in rendered
    assert "empty object {}" in rendered


def test_extraction_prompt_preserves_complete_role_bullet_evidence() -> None:
    prompt = RESUME_EXTRACTION_PROMPT.format(resume_text="").lower()
    work_history = prompt.split("- work_history ", 1)[1].split(
        "- preferred_roles ", 1
    )[0]

    assert "2-5" not in work_history
    assert "2â€“5" not in work_history
    assert "every verified/listed work bullet" in work_history
    assert "preserve role boundaries" in work_history
    assert "do not merge, summarize, or omit bullets" in work_history


def test_retry_contract_covers_all_profile_fields_and_server_role_order() -> None:
    retry = _source_contract_retry_prompt(RESUME_EXTRACTION_PROMPT)

    assert "Every CandidateProfile field must contain normalized human-readable values" in retry
    assert "no request-local cN alias may appear anywhere in the profile" in retry
    for section in ("skills", "education", "certifications", "licenses", "languages", "projects"):
        assert section in retry
    assert "role order is derived by the server" in retry
    assert "Only source_references.work_history" not in retry


@pytest.mark.parametrize(
    "contradiction",
    [
        "links may be null",
        "projects may be null",
        "scalar projects may be null",
        "projects are allowed to be null",
        "projects are permitted to be null",
        "links are allowed to be null",
        "links are permitted to be null",
        "technologies are allowed to be null",
        "achievements are allowed to be null",
        "technologies may use null",
        "achievements should be null",
        "return null for missing projects",
        "unknown list fields can be null",
    ],
)
def test_container_null_guard_rejects_contradictory_guidance(
    contradiction: str,
) -> None:
    with pytest.raises(AssertionError):
        _assert_no_container_null_guidance(contradiction)
