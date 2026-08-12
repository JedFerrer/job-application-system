"""Focused tests for canonical source-resume verification."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.domain.resume.source_content import (
    SOURCE_VALIDATION_VERSION,
    source_fingerprint,
    verify_source_document,
)

pytestmark = pytest.mark.unit


def _doc(*items: object) -> dict[str, object]:
    return {"sections": [{"text": item, "order": index} for index, item in enumerate(items)]}


def _role(*, company: str, title: str, highlight: str) -> dict[str, object]:
    return {
        "work_history": [
            {
                "order": 0,
                "company": {"text": company, "order": 0},
                "title": {"text": title, "order": 1},
                "highlights": [{"text": highlight, "order": 2}],
            }
        ]
    }


def test_exact_literal_and_raw_offsets_are_verified() -> None:
    raw = "Summary\nBuilt FastAPI APIs.\n"
    result = verify_source_document(_doc("Built FastAPI APIs."), raw)
    assert result.status == "verified"
    assert result.payload is not None
    item = result.payload.document.sections[0]
    assert raw[item.start_offset : item.end_offset] == item.text
    assert item.fingerprint == source_fingerprint(item.text)
    assert SOURCE_VALIDATION_VERSION == "source-verifier-v1"


def test_parser_metadata_is_replaced_by_server_verified_metadata() -> None:
    result = verify_source_document(
        {
            "sections": [
                {
                    "text": "Built APIs.",
                    "order": 0,
                    "source_id": "00000000-0000-0000-0000-000000000001",
                    "start_offset": 999,
                    "end_offset": 1000,
                    "fingerprint": "forged",
                    "occurrence_ordinal": 99,
                }
            ]
        },
        "Built APIs.",
    )
    assert result.status == "verified"
    assert result.payload is not None
    item = result.payload.document.sections[0]
    assert str(item.source_id) != "00000000-0000-0000-0000-000000000001"
    assert item.start_offset == 0
    assert item.end_offset == len("Built APIs.")
    assert item.occurrence_ordinal == 0
    assert item.fingerprint == source_fingerprint(item.text)


@pytest.mark.parametrize(
    ("raw", "candidate"),
    [
        ("Cafe\u0301 Engineer", "Café Engineer"),
        ("Built\u00a0FastAPI\u00a0APIs", "Built FastAPI APIs"),
        ("Built  FastAPI\r\nAPIs", "Built FastAPI\nAPIs"),
    ],
)
def test_allowed_unicode_whitespace_and_soft_wrap_normalization(
    raw: str, candidate: str
) -> None:
    result = verify_source_document(_doc(candidate), raw)
    assert result.status == "verified"
    assert result.payload is not None
    item = result.payload.document.sections[0]
    assert raw[item.start_offset : item.end_offset]
    assert item.fingerprint == source_fingerprint(candidate)


def test_plain_string_and_missing_order_are_rejected() -> None:
    assert verify_source_document({"sections": ["Built APIs"]}, "Built APIs").status == "invalid"
    assert (
        verify_source_document(
            {"sections": [{"text": "Built APIs"}]}, "Built APIs"
        ).status
        == "invalid"
    )


@pytest.mark.parametrize(
    ("raw", "candidate"),
    [
        ("First\nSecond", "FirstSecond"),
        ("Python\nDocker", "PythonDocker"),
        ("Built APIs.\nManaged deployments.", "Built APIs.Managed deployments."),
        ("First Second\nThird", "First SecondThird"),
    ],
)
def test_unproven_newline_merges_are_rejected(raw: str, candidate: str) -> None:
    assert verify_source_document(_doc(candidate), raw).status == "invalid"


@pytest.mark.parametrize(
    ("raw", "candidate"),
    [
        ("First\nSecond", "First Second"),
        ("Python\nDocker", "Python Docker"),
        ("Heading\nBody item", "Heading Body item"),
        ("One\r\nTwo", "One Two"),
        ("One\n\nTwo", "One Two"),
    ],
)
def test_semantic_newlines_cannot_become_candidate_spaces(
    raw: str, candidate: str
) -> None:
    assert verify_source_document(_doc(candidate), raw).status == "invalid"


def test_crlf_and_lf_boundary_representation_is_equivalent() -> None:
    result = verify_source_document(_doc("One\nTwo"), "One\r\nTwo")
    assert result.status == "verified"


def test_repeated_horizontal_whitespace_remains_equivalent() -> None:
    result = verify_source_document(_doc("First Second"), "First    Second")
    assert result.status == "verified"


@pytest.mark.parametrize(
    ("raw", "candidate"),
    [
        ("• Built APIs.", "Built APIs."),
        ("- Built APIs.", "Built APIs."),
        ("– Built APIs.", "Built APIs."),
        ("— Built APIs.", "Built APIs."),
        ("* Built APIs.", "Built APIs."),
        ("1. Built APIs.", "Built APIs."),
    ],
)
def test_structural_list_markers_are_not_part_of_bullet_text(
    raw: str, candidate: str
) -> None:
    result = verify_source_document(_doc(candidate), raw)
    assert result.status == "verified"
    assert result.payload is not None
    assert result.payload.document.sections[0].text == candidate


def test_marker_like_punctuation_inside_prose_is_not_stripped() -> None:
    assert verify_source_document(_doc("Built - APIs."), "Built - APIs.").status == "verified"


@pytest.mark.parametrize(
    ("raw", "candidate"),
    [
        ("First bullet.", "First"),
        ("Built APIs", "APIs"),
        ("Hello Built", "Built"),
        ("Built backend services", "backend"),
        ("First Second", "First Second"),
    ],
)
def test_whitespace_delimited_fragments_are_not_complete_source_items(
    raw: str, candidate: str
) -> None:
    result = verify_source_document(_doc(candidate), raw)
    assert result.status == ("verified" if raw == candidate else "invalid")


@pytest.mark.parametrize("marker", ["•", "-", "–", "—", "*", "1.", "2)"])
def test_ordered_and_unordered_markers_require_line_start(marker: str) -> None:
    assert verify_source_document(
        _doc("Built FastAPI services"), f"{marker} Built FastAPI services"
    ).status == "verified"
    assert verify_source_document(
        _doc("Built FastAPI services"), f"Existing {marker} Built FastAPI services"
    ).status == "invalid"


def test_cross_category_global_order_is_verified() -> None:
    result = verify_source_document(
        {
            "sections": [{"text": "Skills", "order": 0}],
            "skills": [{"text": "Python", "order": 1}],
        },
        "Skills\nPython",
    )
    assert result.status == "verified"
    assert result.payload is not None
    assert result.payload.document.sections[0].start_offset == 0
    assert result.payload.document.skills[0].start_offset == 7


def test_declared_global_order_conflicting_with_raw_text_fails() -> None:
    result = verify_source_document(
        {
            "sections": [{"text": "Skills", "order": 1}],
            "skills": [{"text": "Python", "order": 0}],
        },
        "Skills\nPython",
    )
    assert result.status == "invalid"


def test_duplicate_global_order_is_rejected() -> None:
    result = verify_source_document(
        {
            "sections": [{"text": "Skills", "order": 0}],
            "skills": [{"text": "Python", "order": 0}],
        },
        "Skills\nPython",
    )
    assert result.status == "invalid"


def test_summary_skills_and_work_project_order_is_global() -> None:
    result = verify_source_document(
        {
            "summary": {"text": "Summary", "order": 0},
            "skills": [{"text": "Python", "order": 1}],
                "work_history": [
                    {
                        "company": {"text": "Acme", "order": 2},
                        "description": {"text": "Built APIs", "order": 3},
                        "highlights": [{"text": "Shipped", "order": 4}],
                    }
                ],
            "projects": [{"order": 5, "name": {"text": "Portal", "order": 5}}],
        },
        "Summary\nPython\nAcme\nBuilt APIs\nShipped\nPortal",
    )
    assert result.status == "verified"


def test_duplicate_items_get_distinct_occurrences_and_ids() -> None:
    raw = "Built APIs.\nBuilt APIs."
    result = verify_source_document(_doc("Built APIs.", "Built APIs."), raw)
    assert result.status == "verified"
    assert result.payload is not None
    first, second = result.payload.document.sections
    assert first.source_id != second.source_id
    assert first.occurrence_ordinal == 0
    assert second.occurrence_ordinal == 1
    assert (first.start_offset, first.end_offset) != (second.start_offset, second.end_offset)
    UUID(str(first.source_id))
    UUID(str(second.source_id))


def test_single_candidate_for_repeated_raw_text_is_ambiguous() -> None:
    result = verify_source_document(_doc("Built APIs."), "Built APIs.\nBuilt APIs.")
    assert result.status == "invalid"
    assert result.errors


def test_source_order_and_overlap_are_rejected() -> None:
    raw = "First bullet. Second bullet."
    assert verify_source_document(_doc("Second bullet.", "First bullet."), raw).status == "invalid"
    assert verify_source_document(_doc("First", "First bullet."), raw).status == "invalid"


@pytest.mark.parametrize(
    "candidate",
    [
        "Invented technology",
        "First bullet. Second bullet.",
        "First bullet",
        42,
    ],
)
def test_unverifiable_or_malformed_candidates_fail_safe(candidate: object) -> None:
    result = verify_source_document(_doc(candidate), "First bullet.\nSecond bullet.")
    assert result.status == "invalid"
    assert result.errors


def test_empty_source_has_safe_invalid_state() -> None:
    result = verify_source_document({}, "\n\n")
    assert result.status == "invalid"
    assert result.payload is None
    assert "empty_raw_text" in result.errors


def test_role_metadata_is_verified_as_source_text() -> None:
    raw = "Acme Corp\nEngineer\nBuilt APIs."
    result = verify_source_document(
        _role(company="Acme Corp", title="Engineer", highlight="Built APIs."), raw
    )
    assert result.status == "verified"
    assert result.payload is not None
    role = result.payload.document.work_history[0]
    assert role.company is not None and role.company.start_offset == 0
    assert (
        role.title is not None
        and raw[role.title.start_offset : role.title.end_offset] == "Engineer"
    )


def test_unverified_role_metadata_invalidates_source() -> None:
    result = verify_source_document(
        _role(company="Invented Corp", title="Engineer", highlight="Built APIs."),
        "Acme Corp\nEngineer\nBuilt APIs.",
    )
    assert result.status == "invalid"
    assert result.payload is None


def test_fingerprint_is_stable_across_allowed_normalization() -> None:
    assert source_fingerprint("Built  FastAPI APIs") == source_fingerprint(
        "Built FastAPI APIs"
    )
