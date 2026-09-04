"""Focused tests for canonical source-resume verification."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest

from app.domain.resume.schemas import (
    SourceResumeArtifact,
    SourceResumeContentDocumentV2,
    SourceResumeContentDocumentV3,
    SourceResumeContentDocumentV4,
    SourceResumeDocument,
    SourceResumeLayoutReferencesV3,
    SourceResumeLayoutReferencesV4,
)
from app.domain.resume.source_content import (
    SOURCE_VALIDATION_VERSION,
    InvalidReferenceDiagnostic,
    build_source_candidate_ledger,
    canonicalize_source_layout_v5,
    classify_source_artifacts,
    has_usable_tailoring_semantics,
    materialize_source_document,
    materialize_source_document_v3,
    materialize_source_document_v4,
    resolve_source_layout,
    resolve_source_layout_v3,
    resolve_source_layout_v4,
    source_fingerprint,
    verify_source_candidate_ledger,
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


def test_ledger_groups_wrapped_summary_into_one_logical_candidate() -> None:
    ledger = build_source_candidate_ledger(
        "PROFESSIONAL SUMMARY\n"
        "Full-stack engineer with backend focus and\n"
        "automation pipeline experience.\n"
        "TECHNICAL STACK\n"
        "• Python, FastAPI\n"
    )

    assert [candidate.text for candidate in ledger.candidates] == [
        "PROFESSIONAL SUMMARY",
        "Full-stack engineer with backend focus and",
        "automation pipeline experience.",
        "TECHNICAL STACK",
        "Python, FastAPI",
    ]
    assert verify_source_candidate_ledger(
        ledger,
        "PROFESSIONAL SUMMARY\nFull-stack engineer with backend focus and\n"
        "automation pipeline experience.\nTECHNICAL STACK\n• Python, FastAPI\n",
    ) == ()


def test_ledger_groups_wrapped_bullet_into_one_logical_candidate() -> None:
    ledger = build_source_candidate_ledger(
        "PROFESSIONAL EXPERIENCE\n"
        "• Built an API with FastAPI,\n"
        "  PostgreSQL, and Redis.\n"
        "• Added caching and monitoring.\n"
    )

    assert [candidate.text for candidate in ledger.candidates] == [
        "PROFESSIONAL EXPERIENCE",
        "Built an API with FastAPI, PostgreSQL, and Redis.",
        "Added caching and monitoring.",
    ]


def test_v4_preserves_summary_block_and_core_headings() -> None:
    raw = (
        "PROFESSIONAL SUMMARY\nFirst summary line\nSummary continuation\n"
        "TECHNICAL STACK\n• Python\n"
        "PROFESSIONAL EXPERIENCE\nAcme - Engineer\n• Built APIs"
    )
    ledger = build_source_candidate_ledger(raw)
    result = resolve_source_layout_v4(
        {
            "summary": "c1",
            "summary_lines": ["c2"],
            "section_headings": {
                "summary": "c0",
                "skills": "c3",
                "work_history": "c5",
            },
            "skills": ["c4"],
            "work_history": [{"header": "c6", "highlights": ["c7"]}],
        },
        ledger,
    )
    assert result.status == "verified"
    assert result.layout is not None
    document = materialize_source_document_v4(
        SourceResumeContentDocumentV4(candidates=ledger.candidates, layout=result.layout)
    )
    assert document is not None
    assert [item.text for item in document.summary_lines] == ["Summary continuation"]
    assert document.section_headings["skills"].text == "TECHNICAL STACK"


@pytest.mark.parametrize(
    "innovation_heading",
    ["INNOVATION & SIDE PROJECTS", "INNOVATION AND SIDE PROJECTS"],
)
def test_v5_canonicalizer_prevents_cross_section_and_work_duplicates(
    innovation_heading: str,
) -> None:
    raw = (
        "Ada Example\n"
        "PROFESSIONAL SUMMARY\n"
        "Backend engineer with API experience.\n"
        "TECHNICAL SKILLS\n"
        "Python\n"
        f"{innovation_heading}\n"
        "AI Tutor App: Built a grounded tutoring platform.\n"
        "PROFESSIONAL EXPERIENCE\n"
        "Acme - Engineer\n"
        "• Built APIs\n"
        "PROJECTS\n"
        "Portal\n"
        "CERTIFICATIONS\n"
        "Cloud certificate"
    )
    ledger = build_source_candidate_ledger(raw)
    layout = canonicalize_source_layout_v5(
        {
            "contact": {"name": "c0"},
            "links": {},
            "summary": None,
            "summary_lines": [],
            "section_headings": {},
            "skills": ["c8", "c10"],
            "sections": [],
            "work_history": [{
                "order": 0, "header": "c8", "context": ["c9"],
                "responsibilities_heading": None, "highlights": ["c9"],
            }],
            "projects": [{
                "order": 0, "name": "c11", "description": "c11", "url": None,
                "technologies": [], "achievements": [],
            }],
            "education": [],
            "certifications": ["c13"],
            "licenses": [],
            "languages": [],
            "named_sections": [],
        },
        ledger,
    )
    result = resolve_source_layout_v4(layout, ledger, classify_source_artifacts(ledger))
    assert result.status == "verified"
    assert result.layout is not None
    resolved = cast(SourceResumeLayoutReferencesV4, result.layout)
    assert resolved.skills == [ledger.candidates[4].source_id]
    assert resolved.section_headings["named:0"] == ledger.candidates[5].source_id
    assert resolved.named_sections[0].items == [ledger.candidates[6].source_id]
    assert len(resolved.work_history[0].highlights) == 1


def test_v4_accepts_and_materializes_projects_heading() -> None:
    raw = "PROJECTS\nAPI Portal\nDescription"
    ledger = build_source_candidate_ledger(raw)
    result = resolve_source_layout_v4(
        {
            "section_headings": {"projects": "c0"},
            "projects": [{"name": "c1", "description": "c2"}],
        },
        ledger,
    )
    assert result.status == "verified"
    assert result.layout is not None
    document = materialize_source_document_v4(
        SourceResumeContentDocumentV4(candidates=ledger.candidates, layout=result.layout)
    )
    assert document is not None
    assert document.section_headings["projects"].text == "PROJECTS"


def test_v4_ignores_null_optional_headings_from_strict_selector() -> None:
    ledger = build_source_candidate_ledger(
        "PROFESSIONAL EXPERIENCE\nAcme - Engineer\nBuilt APIs"
    )
    result = resolve_source_layout_v4(
        {
            "section_headings": {
                "summary": None,
                "skills": None,
                "work_history": "c0",
                "education": None,
            },
            "work_history": [{"header": "c1", "highlights": ["c2"]}],
        },
        ledger,
    )
    assert result.status == "verified"
    assert result.layout is not None
    layout = cast(SourceResumeLayoutReferencesV4, result.layout)
    assert layout.section_headings == {"work_history": ledger.candidates[0].source_id}


def test_v3_server_owns_one_unclassified_candidate_without_source_content() -> None:
    private_candidate = "Private source candidate"
    ledger = build_source_candidate_ledger(f"Built APIs\n{private_candidate}")
    result = resolve_source_layout_v3({"sections": ["c0"]}, ledger)
    assert result.status == "verified"
    assert result.layout is not None
    layout = cast(SourceResumeLayoutReferencesV3, result.layout)
    assert layout.unclassified == [ledger.candidates[1].source_id]
    assert private_candidate not in repr(layout.unclassified)
    artifacts = classify_source_artifacts(ledger)
    assert artifacts == []


def test_v3_server_owns_all_unclassified_candidates_in_source_order() -> None:
    ledger = build_source_candidate_ledger("First\nSecond\nThird\nFourth")

    result = resolve_source_layout_v3({"sections": ["c1"]}, ledger)

    assert result.status == "verified"
    assert result.layout is not None
    layout = cast(SourceResumeLayoutReferencesV3, result.layout)
    assert layout.unclassified == [
        ledger.candidates[0].source_id,
        ledger.candidates[2].source_id,
        ledger.candidates[3].source_id,
    ]


def test_v3_rejects_a_malformed_role_without_an_uncaught_assertion() -> None:
    ledger = build_source_candidate_ledger("Acme - Engineer\nBuilt APIs")

    result = resolve_source_layout_v3({"work_history": ["not-a-role"]}, ledger)

    assert result.status == "invalid"
    assert result.invalid_reference_diagnostics == (
        InvalidReferenceDiagnostic(
            reason="role_object_expected", path="work_history[0]", alias=None
        ),
    )


@pytest.mark.parametrize(
    ("layout", "reason", "path"),
    [
        (
            {"work_history": [{"header": "c0", "highlights": ["c1"], "unexpected": "ignored"}]},
            "unknown_role_field",
            "work_history[0]",
        ),
        (
            {"named_sections": [{"heading": "c0", "items": ["c1"], "unexpected": "ignored"}]},
            "unknown_named_section_field",
            "named_sections[0]",
        ),
    ],
)
def test_v3_rejects_unknown_nested_fields_without_leaking_values(
    layout: dict[str, object], reason: str, path: str
) -> None:
    ledger = build_source_candidate_ledger("Role\nBuilt APIs\nAwards\nAward item")

    result = resolve_source_layout_v3(layout, ledger)

    assert result.status == "invalid"
    assert result.errors == ("invalid_source_references",)
    assert result.invalid_reference_diagnostics == (
        InvalidReferenceDiagnostic(reason, path, None),
    )
    assert "ignored" not in repr(result.invalid_reference_diagnostics)


def test_v3_accepts_minimal_optional_nested_objects() -> None:
    ledger = build_source_candidate_ledger("Role\nBuilt APIs\nAwards\nAward item")

    result = resolve_source_layout_v3(
        {
            "work_history": [{"header": "c0", "highlights": ["c1"]}],
            "named_sections": [{"heading": "c2", "items": ["c3"]}],
        },
        ledger,
    )

    assert result.status == "verified"
    assert result.layout is not None
    assert len(result.layout.work_history) == 1
    layout = cast(SourceResumeLayoutReferencesV3, result.layout)
    assert len(layout.named_sections) == 1


def test_v3_long_partial_annotation_is_lossless_and_semantically_viable() -> None:
    raw = "Acme - Engineer\nBuilt FastAPI APIs\n" + "\n".join(
        f"Unclassified source line {index}" for index in range(160)
    )
    ledger = build_source_candidate_ledger(raw)

    result = resolve_source_layout_v3(
        {"work_history": [{"header": "c0", "highlights": ["c1"]}]}, ledger
    )

    assert result.status == "verified"
    assert result.layout is not None
    layout = cast(SourceResumeLayoutReferencesV3, result.layout)
    assert has_usable_tailoring_semantics(layout)
    assert len(layout.unclassified) == 160
    assert layout.unclassified[0] == ledger.candidates[2].source_id
    assert layout.unclassified[-1] == ledger.candidates[-1].source_id


def test_v3_materializes_unclassified_without_assigning_role_membership() -> None:
    ledger = build_source_candidate_ledger(
        "Acme - Engineer\nRole context\nUnclassified source line\nBuilt APIs"
    )
    result = resolve_source_layout_v3(
        {
            "work_history": [
                {"header": "c0", "context": ["c1"], "highlights": ["c3"]}
            ]
        },
        ledger,
    )

    assert result.layout is not None
    document = materialize_source_document_v3(
        SourceResumeContentDocumentV3(candidates=ledger.candidates, layout=result.layout)
    )
    assert document is not None
    assert [item.text for item in document.unclassified] == ["Unclassified source line"]
    assert [item.text for item in document.work_history[0].context_items] == ["Role context"]
    assert [item.text for item in document.work_history[0].highlights] == ["Built APIs"]


def test_v3_mechanical_separator_can_be_explicitly_accounted() -> None:
    ledger = build_source_candidate_ledger("Built APIs\n---")
    artifacts = classify_source_artifacts(ledger)
    assert len(artifacts) == 1
    result = resolve_source_layout_v3(
        {"sections": ["c0"]}, ledger, artifacts=artifacts
    )
    assert result.status == "verified"
    assert result.missing_candidate_orders == ()


def _v3_layout() -> dict[str, object]:
    return {
        "sections": ["c6"],
        "work_history": [{
            "header": "c0",
            "context": ["c1"],
            "responsibilities_heading": "c2",
            "highlights": ["c3"],
        }],
        "named_sections": [{"heading": "c4", "items": ["c5"]}],
    }


@pytest.mark.parametrize(
    "mutate, artifacts, error",
    [
        (
            lambda layout: layout["work_history"][0].update({"highlights": ["c1", "c3"]}),
            [],
            "invalid_source_references",
        ),
        (
            lambda layout: layout["named_sections"][0].update({"items": ["c1", "c5"]}),
            [],
            "invalid_source_references",
        ),
        (
            lambda layout: layout["named_sections"][0].update({"items": ["c3", "c5"]}),
            [],
            "invalid_source_references",
        ),
        (
            lambda layout: layout["named_sections"].append({"heading": "c4", "items": []}),
            [],
            "invalid_source_references",
        ),
        (
            lambda layout: layout.update({"contact": {"location": "c6"}}),
            [],
            "invalid_source_references",
        ),
    ],
)
def test_v3_rejects_duplicate_canonical_candidate_ownership(
    mutate: object, artifacts: list[SourceResumeArtifact], error: str
) -> None:
    ledger = build_source_candidate_ledger(
        "Role\nContext\nResponsibilities:\nBullet\nAwards\nAward item\nOther"
    )
    layout = _v3_layout()
    assert callable(mutate)
    mutate(layout)

    result = resolve_source_layout_v3(layout, ledger, artifacts=artifacts)

    assert result.status == "invalid"
    assert result.errors == (error,)


def test_v3_duplicate_diagnostics_identify_alias_and_conflicting_paths() -> None:
    ledger = build_source_candidate_ledger(
        "Role\nContext\nResponsibilities:\nBullet\nAwards\nAward item\nOther"
    )
    layout = _v3_layout()
    layout["sections"] = ["c6", "c6"]

    result = resolve_source_layout_v3(layout, ledger)

    assert result.errors == ("invalid_source_references",)
    assert result.invalid_reference_diagnostics == (
        InvalidReferenceDiagnostic("duplicate_exclusive_reference", "sections[0]", "c6"),
        InvalidReferenceDiagnostic("duplicate_exclusive_reference", "sections[1]", "c6"),
    )


def test_v3_allows_one_physical_line_to_supply_multiple_metadata_fields() -> None:
    ledger = build_source_candidate_ledger("Ada\nada@example.com")

    result = resolve_source_layout_v3(
        {"contact": {"name": "c0", "location": "c0"}, "links": {"profiles": "c0"}},
        ledger,
    )

    assert result.status == "verified"


def test_v4_reports_project_alias_selected_as_skill() -> None:
    ledger = build_source_candidate_ledger(
        "Ada\nTECHNICAL STACK\nPython\nPROJECTS\nResume Matcher"
    )

    result = resolve_source_layout_v4(
        {
            "contact": {"name": "c0"},
            "section_headings": {"skills": "c1", "projects": "c3"},
            "skills": ["c2", "c4"],
            "projects": [{"name": "c4"}],
        },
        ledger,
    )

    assert result.status == "invalid"
    assert InvalidReferenceDiagnostic(
        "section_range_conflict", "skills[1]", "c4", "projects"
    ) in result.invalid_reference_diagnostics


def test_v3_rejects_candidate_owned_by_content_and_artifact() -> None:
    ledger = build_source_candidate_ledger(
        "Role\nContext\nResponsibilities:\nBullet\nAwards\nAward item\nOther"
    )
    artifact = SourceResumeArtifact(
        source_id=ledger.candidates[3].source_id, reason="mechanical_separator"
    )

    result = resolve_source_layout_v3(_v3_layout(), ledger, artifacts=[artifact])

    assert result.status == "invalid"
    assert result.errors == ("duplicate_source_candidate_ownership",)
    assert result.missing_candidate_orders == ()


@pytest.mark.parametrize(
    ("mutate", "reason", "path", "alias"),
    [
        (
            lambda layout: layout["work_history"][0].update({"header": "c99"}),
            "unknown_candidate_alias", "work_history[0].header", "c99",
        ),
        (
            lambda layout: layout["work_history"][0].update({"context": "c1"}),
            "reference_list_expected", "work_history[0].context", None,
        ),
        (
            lambda layout: layout["work_history"][0].update({"context": ["c99"]}),
            "unknown_candidate_alias", "work_history[0].context[0]", "c99",
        ),
        (
            lambda layout: layout["work_history"][0].update({"responsibilities_heading": 3}),
            "responsibilities_heading_expected",
            "work_history[0].responsibilities_heading",
            None,
        ),
        (
            lambda layout: layout["named_sections"][0].update({"items": ["c99"]}),
            "unknown_candidate_alias", "named_sections[0].items[0]", "c99",
        ),
    ],
)
def test_v3_invalid_reference_diagnostics_are_structural_and_safe(
    mutate: object, reason: str, path: str, alias: str | None
) -> None:
    ledger = build_source_candidate_ledger(
        "Role\nContext\nResponsibilities:\nBullet\nAwards\nAward item\nOther"
    )
    layout = _v3_layout()
    assert callable(mutate)
    mutate(layout)

    result = resolve_source_layout_v3(layout, ledger)

    assert result.errors == ("invalid_source_references",)
    assert result.invalid_reference_diagnostics == (
        InvalidReferenceDiagnostic(reason, path, alias),
    )
    assert "Role" not in repr(result.invalid_reference_diagnostics)


def test_v3_invalid_reference_diagnostics_cover_order_duplicate_and_schema_privacy() -> None:
    ordered_ledger = build_source_candidate_ledger("First\nSecond")
    ordered = resolve_source_layout_v3(
        {"sections": ["c1", "c0"]}, ordered_ledger
    )
    duplicate = resolve_source_layout_v3(
        {"contact": {"email": "c0"}, "summary": "c0"},
        build_source_candidate_ledger("Private contact"),
    )
    private_value = "Private invalid project order"
    schema = resolve_source_layout_v3(
        {"sections": ["c0"], "projects": [{"order": private_value}]},
        build_source_candidate_ledger("First"),
    )

    assert ordered.status == "verified"
    assert ordered.layout is not None
    assert ordered.layout.sections == [
        ordered_ledger.candidates[0].source_id,
        ordered_ledger.candidates[1].source_id,
    ]
    assert duplicate.invalid_reference_diagnostics[0].reason == "metadata_content_overlap"
    assert schema.invalid_reference_diagnostics[0] == InvalidReferenceDiagnostic(
        "schema_validation_failed", "projects[0].order"
    )
    assert private_value not in repr(schema.invalid_reference_diagnostics)


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


def test_v2_candidate_ledger_is_server_owned_and_line_bounded() -> None:
    raw = "Ada Lovelace\r\n\r\n- Built APIs.\r\nBuilt APIs."
    ledger = build_source_candidate_ledger(raw)

    assert [candidate.order for candidate in ledger.candidates] == [0, 1, 2]
    assert [candidate.text for candidate in ledger.candidates] == [
        "Ada Lovelace",
        "Built APIs.",
        "Built APIs.",
    ]
    assert ledger.candidates[1].occurrence_ordinal == 0
    assert ledger.candidates[2].occurrence_ordinal == 1
    assert ledger.candidates[1].start_offset > ledger.candidates[0].end_offset
    assert ledger.candidates[1].fingerprint == source_fingerprint("Built APIs.")
    assert verify_source_candidate_ledger(ledger, raw) == ()


def test_v2_ledger_rejects_cross_line_candidate_tampering() -> None:
    ledger = build_source_candidate_ledger("First\nSecond")
    tampered = ledger.candidates[0].model_copy(update={"text": "First Second"})
    invalid = ledger.model_copy(update={"candidates": [tampered, ledger.candidates[1]]})

    assert "candidate_text_mismatch" in verify_source_candidate_ledger(invalid, "First\nSecond")


def test_v2_ledger_rejects_occurrence_and_order_tampering() -> None:
    raw = "Built APIs.\nBuilt APIs."
    ledger = build_source_candidate_ledger(raw)
    tampered = ledger.model_copy(
        update={
            "candidates": [
                ledger.candidates[0].model_copy(update={"occurrence_ordinal": 1}),
                ledger.candidates[1].model_copy(update={"order": 3}),
            ]
        }
    )

    errors = verify_source_candidate_ledger(tampered, raw)

    assert "candidate_order_not_contiguous" in errors
    assert "candidate_occurrence_mismatch" in errors


def test_v2_layout_resolves_aliases_without_accepting_model_prose_or_metadata() -> None:
    ledger = build_source_candidate_ledger("Acme - Engineer (2020-2022)\nBuilt APIs.")
    result = resolve_source_layout(
        {
            "work_history": [
                {
                    "order": 0,
                    "header": "c0",
                    "highlights": ["c1"],
                }
            ]
        },
        ledger,
    )

    assert result.status == "verified"
    assert result.layout is not None
    document = materialize_source_document(ledger.model_copy(update={"layout": result.layout}))
    assert document is not None
    assert document.work_history[0].company is not None
    assert document.work_history[0].company.text == "Acme - Engineer (2020-2022)"
    assert document.work_history[0].title is not None
    assert document.work_history[0].title.text == document.work_history[0].company.text
    assert document.work_history[0].highlights[0].text == "Built APIs."


@pytest.mark.parametrize(
    "layout",
    [
        {},
        {"work_history": [{"highlights": ["c0"]}]},
        {"work_history": [{"header": "unknown", "highlights": []}]},
        {"work_history": [{"header": "c0", "highlights": ["c0"]}]},
        {"work_history": [{"header": "c0", "highlights": ["c2", "c1"]}]},
        {"work_history": [{"header": "c0", "text": "forbidden", "highlights": []}]},
    ],
)
def test_v2_layout_rejects_unknown_duplicate_out_of_order_or_text_fields(
    layout: dict[str, object],
) -> None:
    ledger = build_source_candidate_ledger("Acme\nEngineer\nBuilt APIs.")

    result = resolve_source_layout(layout, ledger)

    assert result.status == "invalid"


def test_v2_layout_materializes_two_roles_without_splitting_headers() -> None:
    raw = "Acme - Engineer (2020-2022)\nBuilt APIs.\nBeta - Lead (2022-2024)\nLed releases."
    ledger = build_source_candidate_ledger(raw)
    result = resolve_source_layout(
        {
            "work_history": [
                {"order": 0, "header": "c0", "highlights": ["c1"]},
                {"order": 1, "header": "c2", "highlights": ["c3"]},
            ]
        },
        ledger,
    )

    assert result.status == "verified"
    assert result.layout is not None
    document = materialize_source_document(ledger.model_copy(update={"layout": result.layout}))
    assert document is not None
    assert [role.company.text for role in document.work_history if role.company] == [
        "Acme - Engineer (2020-2022)",
        "Beta - Lead (2022-2024)",
    ]
    assert [role.highlights[0].text for role in document.work_history] == [
        "Built APIs.",
        "Led releases.",
    ]
    assert [role.source_id for role in document.work_history] == [
        ledger.candidates[0].source_id,
        ledger.candidates[2].source_id,
    ]
    repeated = materialize_source_document(ledger.model_copy(update={"layout": result.layout}))
    assert repeated is not None
    assert [role.source_id for role in repeated.work_history] == [
        role.source_id for role in document.work_history
    ]


def test_v2_layout_preserves_all_late_role_highlights_without_profile_identity() -> None:
    bullets = [
        "Built APIs",
        "Added caching",
        "Improved latency",
        "Automated releases",
        "Hardened authentication",
        "Owned observability",
        "Documented services",
    ]
    raw = "Acme Platform - Engineer\n" + "\n".join(bullets)
    ledger = build_source_candidate_ledger(raw)
    result = resolve_source_layout(
        {
            "work_history": [
                {"header": "c0", "highlights": [f"c{index}" for index in range(1, 8)]}
            ]
        },
        ledger,
    )

    assert result.status == "verified"
    assert result.layout is not None
    document = materialize_source_document(ledger.model_copy(update={"layout": result.layout}))
    assert document is not None
    assert [item.text for item in document.work_history[0].highlights] == bullets
    assert [item.order for item in document.work_history[0].highlights] == list(range(1, 8))
    assert verify_source_candidate_ledger(ledger, raw) == ()


def test_v2_layout_derives_role_order_from_header_candidates() -> None:
    raw = "Acme - Engineer\nBuilt APIs.\nBeta - Lead\nLed releases."
    ledger = build_source_candidate_ledger(raw)
    result = resolve_source_layout(
        {
            "work_history": [
                {"order": 99, "header": "c2", "highlights": ["c3"]},
                {"order": 99, "header": "c0", "highlights": ["c1"]},
            ]
        },
        ledger,
    )

    assert result.status == "verified"
    assert result.layout is not None
    assert [role.order for role in result.layout.work_history] == [0, 2]
    document = materialize_source_document(ledger.model_copy(update={"layout": result.layout}))
    assert document is not None
    assert [role.company.text for role in document.work_history if role.company] == [
        "Acme - Engineer",
        "Beta - Lead",
    ]


def test_v2_layout_allows_shared_contact_candidate_for_email_and_phone() -> None:
    ledger = build_source_candidate_ledger("ada@example.com | +1 555 0100")

    result = resolve_source_layout(
        {"contact": {"email": "c0", "phone": "c0"}},
        ledger,
    )

    assert result.status == "verified"
    assert result.layout is not None
    assert result.layout.contact["email"] == result.layout.contact["phone"]
    document = materialize_source_document(ledger.model_copy(update={"layout": result.layout}))
    assert document is not None
    assert document.contact["email"].source_id == document.contact["phone"].source_id


def test_v2_layout_keeps_duplicate_work_bullet_rejected() -> None:
    ledger = build_source_candidate_ledger("Role A\nRole B\nBuilt APIs.")

    result = resolve_source_layout(
        {
            "work_history": [
                {"header": "c0", "highlights": ["c2"]},
                {"header": "c1", "highlights": ["c2"]},
            ]
        },
        ledger,
    )

    assert result.status == "invalid"


@pytest.mark.parametrize(
    "layout",
    [
        {"contact": {"email": "c999"}},
        {"contact": {"email": "c0"}, "summary": "c0"},
    ],
)
def test_v2_layout_rejects_unknown_or_cross_category_duplicate_metadata(
    layout: dict[str, object],
) -> None:
    ledger = build_source_candidate_ledger("Contact line")

    result = resolve_source_layout(layout, ledger)

    assert result.status == "invalid"


MALFORMED_WRAP_RESUME = """Ada Example
ada@example.com
PROFESSIONAL EXPERIENCE
Northwind Analytics - Platform Engineer
Led the billing platform through a multi-year
modernization of core services.
Responsibilities
• Designed an event pipeline that processed
payments across three regions without
losing audit history.
• Coordinated a cutover that preserved
customer invoicing during the

migration window and reduced failed retries.
• Added service-level monitors for checkout.
Contoso Labs - Backend Engineer
Owned API delivery for the catalog service.
Responsibilities
• Built FastAPI services for catalog search.
"""


def _alias_for(ledger: SourceResumeContentDocumentV2, text: str) -> str:
    for candidate in ledger.candidates:
        if candidate.text == text:
            return f"c{candidate.order}"
    raise AssertionError(f"missing source candidate: {text}")


def _empty_v5_layout(*headers: str) -> dict[str, object]:
    return {
        "contact": {},
        "links": {},
        "summary": None,
        "summary_lines": [],
        "section_headings": {},
        "skills": [],
        "sections": [],
        "work_history": [
            {
                "order": index,
                "header": header,
                "context": [],
                "responsibilities_heading": None,
                "highlights": [],
            }
            for index, header in enumerate(headers)
        ],
        "projects": [],
        "education": [],
        "certifications": [],
        "licenses": [],
        "languages": [],
        "named_sections": [],
    }


def _materialize_canonical_roles(
    raw: str, *header_texts: str
) -> tuple[SourceResumeContentDocumentV2, SourceResumeLayoutReferencesV4, SourceResumeDocument]:
    ledger = build_source_candidate_ledger(raw)
    assert verify_source_candidate_ledger(ledger, raw) == ()
    layout = canonicalize_source_layout_v5(
        _empty_v5_layout(*(_alias_for(ledger, header) for header in header_texts)),
        ledger,
    )
    result = resolve_source_layout_v4(layout, ledger, classify_source_artifacts(ledger))
    assert result.status == "verified"
    assert result.layout is not None
    resolved = cast(SourceResumeLayoutReferencesV4, result.layout)
    document = materialize_source_document_v4(
        SourceResumeContentDocumentV4(candidates=ledger.candidates, layout=resolved)
    )
    assert document is not None
    return ledger, resolved, document


def test_malformed_wrap_resume_keeps_continuations_inside_source_items() -> None:
    ledger, layout, document = _materialize_canonical_roles(
        MALFORMED_WRAP_RESUME,
        "Northwind Analytics - Platform Engineer",
        "Contoso Labs - Backend Engineer",
    )
    role_a, role_b = document.work_history

    assert [item.text for item in role_a.context_items] == [
        "Led the billing platform through a multi-year modernization of core services."
    ]
    assert role_a.responsibilities_heading is not None
    assert role_a.responsibilities_heading.text == "Responsibilities"
    assert [item.text for item in role_a.highlights] == [
        (
            "Designed an event pipeline that processed payments across three "
            "regions without losing audit history."
        ),
        (
            "Coordinated a cutover that preserved customer invoicing during the "
            "migration window and reduced failed retries."
        ),
        "Added service-level monitors for checkout.",
    ]
    assert [item.text for item in role_b.context_items] == [
        "Owned API delivery for the catalog service."
    ]
    assert role_b.responsibilities_heading is not None
    assert role_b.responsibilities_heading.text == "Responsibilities"
    assert [item.text for item in role_b.highlights] == [
        "Built FastAPI services for catalog search."
    ]

    role_a_ids = {
        role_a.source_id,
        *(item.source_id for item in role_a.context_items),
        role_a.responsibilities_heading.source_id,
        *(item.source_id for item in role_a.highlights),
    }
    role_b_ids = {
        role_b.source_id,
        *(item.source_id for item in role_b.context_items),
        role_b.responsibilities_heading.source_id,
        *(item.source_id for item in role_b.highlights),
    }
    assert role_a.source_id == next(
        candidate.source_id
        for candidate in ledger.candidates
        if candidate.text == "Northwind Analytics - Platform Engineer"
    )
    assert role_b.source_id == next(
        candidate.source_id
        for candidate in ledger.candidates
        if candidate.text == "Contoso Labs - Backend Engineer"
    )
    assert not role_a_ids & role_b_ids
    assert [role.order for role in layout.work_history] == [
        next(
            candidate.order
            for candidate in ledger.candidates
            if candidate.text == "Northwind Analytics - Platform Engineer"
        ),
        next(
            candidate.order
            for candidate in ledger.candidates
            if candidate.text == "Contoso Labs - Backend Engineer"
        ),
    ]
    assert [item.order for item in role_a.highlights] == sorted(
        item.order for item in role_a.highlights
    )
    context_text = " ".join(item.text for item in role_a.context_items)
    highlight_text = " ".join(item.text for item in role_a.highlights)
    assert "payments across three regions without" not in context_text
    assert "migration window and reduced failed retries" not in context_text
    assert "payments across three regions without" in highlight_text
    assert "migration window and reduced failed retries" in highlight_text
    assert "Owned API delivery for the catalog service." not in highlight_text
    unclassified_text = {item.text for item in document.unclassified}
    assert unclassified_text <= {"Ada Example", "ada@example.com"}
    represented = {
        item.text
        for item in (
            *role_a.context_items,
            *role_a.highlights,
            *role_b.context_items,
            *role_b.highlights,
        )
    }
    assert not unclassified_text & represented


def test_ledger_joins_unindented_lowercase_bullet_continuation() -> None:
    ledger = build_source_candidate_ledger(
        "PROFESSIONAL EXPERIENCE\n"
        "• Designed an event pipeline that processed\n"
        "payments across three regions.\n"
    )
    assert [candidate.text for candidate in ledger.candidates] == [
        "PROFESSIONAL EXPERIENCE",
        "Designed an event pipeline that processed payments across three regions.",
    ]
    assert ledger.candidates[1].kind == "bullet"


def test_ledger_does_not_join_lowercase_prose_after_complete_bullet() -> None:
    raw = (
        "PROFESSIONAL EXPERIENCE\n"
        "• Built the synchronization process.\n\n"
        "including operational analysis was a separate part of the role.\n"
    )
    ledger = build_source_candidate_ledger(raw)
    assert [candidate.text for candidate in ledger.candidates] == [
        "PROFESSIONAL EXPERIENCE",
        "Built the synchronization process.",
        "including operational analysis was a separate part of the role.",
    ]
    assert [candidate.kind for candidate in ledger.candidates] == [
        "heading",
        "bullet",
        "text",
    ]
    assert verify_source_candidate_ledger(ledger, raw) == ()


def test_complete_bullet_lowercase_prose_is_unclassified_not_context() -> None:
    _, _, document = _materialize_canonical_roles(
        "PROFESSIONAL EXPERIENCE\n"
        "Northwind Analytics - Platform Engineer\n"
        "• Built the synchronization process.\n\n"
        "including operational analysis was a separate part of the role.\n"
        "• Added checkout monitors.\n",
        "Northwind Analytics - Platform Engineer",
    )
    role = document.work_history[0]
    leftover = "including operational analysis was a separate part of the role."
    assert [item.text for item in role.highlights] == [
        "Built the synchronization process.",
        "Added checkout monitors.",
    ]
    assert leftover not in [item.text for item in role.context_items]
    assert leftover not in [item.text for item in role.highlights]
    assert [item.text for item in document.unclassified] == [leftover]


def test_ledger_does_not_join_lowercase_list_item_to_preceding_bullet() -> None:
    ledger = build_source_candidate_ledger(
        "PROFESSIONAL EXPERIENCE\n"
        "• Built the synchronization process.\n"
        "- added monitoring for failed retries.\n"
        "1. documented the cutover checklist.\n"
    )
    assert [candidate.kind for candidate in ledger.candidates] == [
        "heading",
        "bullet",
        "bullet",
        "bullet",
    ]
    assert [candidate.text for candidate in ledger.candidates[1:]] == [
        "Built the synchronization process.",
        "added monitoring for failed retries.",
        "documented the cutover checklist.",
    ]


def test_work_history_narrative_wrap_becomes_one_logical_context_item() -> None:
    raw = (
        "PROFESSIONAL EXPERIENCE\n"
        "Senior Engineer\n"
        "Worked across backend services and\n"
        "internal warehouse automation systems.\n"
        "Responsibilities\n"
        "• Built API integrations for billing.\n"
    )
    ledger, _, document = _materialize_canonical_roles(raw, "Senior Engineer")
    role = document.work_history[0]
    assert [item.text for item in role.context_items] == [
        "Worked across backend services and internal warehouse automation systems."
    ]
    narrative = role.context_items[0]
    source = next(
        candidate
        for candidate in ledger.candidates
        if candidate.source_id == narrative.source_id
    )
    assert source.kind == "text"
    assert raw[source.start_offset:source.end_offset].startswith(
        "Worked across backend services and"
    )
    assert raw[source.start_offset:source.end_offset].endswith(
        "internal warehouse automation systems."
    )
    assert "internal warehouse automation systems." not in [
        item.text
        for item in ledger.candidates
        if item.source_id != narrative.source_id
    ]
    assert verify_source_candidate_ledger(ledger, raw) == ()


def test_ledger_joins_hyphenated_bullet_continuation() -> None:
    ledger = build_source_candidate_ledger(
        "PROFESSIONAL EXPERIENCE\n"
        "• Coordinated a mid-year cutover of the billing-\n"
        "service replica set.\n"
    )
    assert [candidate.text for candidate in ledger.candidates] == [
        "PROFESSIONAL EXPERIENCE",
        "Coordinated a mid-year cutover of the billing- service replica set.",
    ]


def test_ledger_joins_bullet_continuation_across_page_boundary() -> None:
    ledger = build_source_candidate_ledger(
        "PROFESSIONAL EXPERIENCE\n"
        "• Coordinated a cutover that preserved\n"
        "customer invoicing during the\n\n"
        "migration window and reduced failed retries.\n"
    )
    assert [candidate.text for candidate in ledger.candidates] == [
        "PROFESSIONAL EXPERIENCE",
        (
            "Coordinated a cutover that preserved customer invoicing during the "
            "migration window and reduced failed retries."
        ),
    ]


def test_ledger_does_not_join_adjacent_bullets_or_following_heading() -> None:
    ledger = build_source_candidate_ledger(
        "PROFESSIONAL EXPERIENCE\n"
        "• Built the billing API.\n"
        "• Added checkout monitors.\n"
        "Responsibilities\n"
        "• Kept a later bullet independent.\n"
    )
    assert [candidate.kind for candidate in ledger.candidates] == [
        "heading",
        "bullet",
        "bullet",
        "text",
        "bullet",
    ]
    assert [candidate.text for candidate in ledger.candidates[1:]] == [
        "Built the billing API.",
        "Added checkout monitors.",
        "Responsibilities",
        "Kept a later bullet independent.",
    ]


def test_narrative_wrap_stays_narrative_and_does_not_become_a_bullet() -> None:
    _, _, document = _materialize_canonical_roles(
        "PROFESSIONAL EXPERIENCE\n"
        "Northwind Analytics - Platform Engineer\n"
        "Led the billing platform through a multi-year\n"
        "modernization of core services.\n"
        "Responsibilities\n"
        "• Added service-level monitors for checkout.\n",
        "Northwind Analytics - Platform Engineer",
    )
    role = document.work_history[0]
    assert [item.text for item in role.context_items] == [
        "Led the billing platform through a multi-year modernization of core services."
    ]
    assert [item.text for item in role.highlights] == [
        "Added service-level monitors for checkout."
    ]


def test_heading_then_bullets_keeps_heading_out_of_context_and_highlights() -> None:
    _, _, document = _materialize_canonical_roles(
        "PROFESSIONAL EXPERIENCE\n"
        "Northwind Analytics - Platform Engineer\n"
        "Responsibilities\n"
        "• Built the billing API.\n"
        "• Added checkout monitors.\n",
        "Northwind Analytics - Platform Engineer",
    )
    role = document.work_history[0]
    assert role.context_items == []
    assert role.responsibilities_heading is not None
    assert role.responsibilities_heading.text == "Responsibilities"
    assert [item.text for item in role.highlights] == [
        "Built the billing API.",
        "Added checkout monitors.",
    ]


def test_role_transition_does_not_join_final_bullet_to_next_role() -> None:
    ledger, _, document = _materialize_canonical_roles(
        "PROFESSIONAL EXPERIENCE\n"
        "Northwind Analytics - Platform Engineer\n"
        "• Added service-level monitors for checkout.\n"
        "Contoso Labs - Backend Engineer\n"
        "Owned API delivery for the catalog service.\n"
        "• Built FastAPI services for catalog search.\n",
        "Northwind Analytics - Platform Engineer",
        "Contoso Labs - Backend Engineer",
    )
    role_a, role_b = document.work_history
    assert [item.text for item in role_a.highlights] == [
        "Added service-level monitors for checkout."
    ]
    assert role_a.context_items == []
    assert [item.text for item in role_b.context_items] == [
        "Owned API delivery for the catalog service."
    ]
    assert [item.text for item in role_b.highlights] == [
        "Built FastAPI services for catalog search."
    ]
    assert role_b.source_id == next(
        candidate.source_id
        for candidate in ledger.candidates
        if candidate.text == "Contoso Labs - Backend Engineer"
    )


def test_ambiguous_capitalized_continuation_is_preserved_not_attached() -> None:
    _, layout, document = _materialize_canonical_roles(
        "PROFESSIONAL EXPERIENCE\n"
        "Northwind Analytics - Platform Engineer\n"
        "• Added service-level monitors for checkout.\n"
        "Additional program notes remain source-backed.\n"
        "• Built a second independent bullet.\n",
        "Northwind Analytics - Platform Engineer",
    )
    role = document.work_history[0]
    assert [item.text for item in role.highlights] == [
        "Added service-level monitors for checkout.",
        "Built a second independent bullet.",
    ]
    assert "Additional program notes remain source-backed." not in [
        item.text for item in role.context_items
    ]
    assert "Additional program notes remain source-backed." not in [
        item.text for item in role.highlights
    ]
    assert [
        item.text for item in document.unclassified
    ] == ["Additional program notes remain source-backed."]
    notes = next(
        item for item in document.unclassified if item.text.startswith("Additional")
    )
    assert notes.source_id == layout.unclassified[0]


def test_malformed_source_relationship_stays_fail_closed() -> None:
    ledger = build_source_candidate_ledger(
        "PROFESSIONAL EXPERIENCE\nNorthwind Analytics - Platform Engineer\n• Built APIs.\n"
    )
    result = resolve_source_layout_v4(
        {
            "section_headings": {"work_history": "c0"},
            "work_history": [{"header": "c99", "highlights": ["c2"]}],
        },
        ledger,
    )
    assert result.status == "invalid"
    assert result.layout is None
    assert result.errors == ("invalid_source_references",)


def test_reconstruction_preserves_source_ids_and_order() -> None:
    raw = (
        "PROFESSIONAL EXPERIENCE\n"
        "Northwind Analytics - Platform Engineer\n"
        "Led platform delivery.\n"
        "Responsibilities\n"
        "• Built the billing API.\n"
        "• Added checkout monitors.\n"
    )
    ledger, layout, document = _materialize_canonical_roles(
        raw, "Northwind Analytics - Platform Engineer"
    )
    before = {
        candidate.text: (candidate.source_id, candidate.order)
        for candidate in ledger.candidates
    }
    role = document.work_history[0]
    assert (role.source_id, role.order) == before["Northwind Analytics - Platform Engineer"]
    assert role.responsibilities_heading is not None
    assert (
        role.responsibilities_heading.source_id,
        role.responsibilities_heading.order,
    ) == before["Responsibilities"]
    assert [
        (item.source_id, item.order) for item in role.context_items
    ] == [before["Led platform delivery."]]
    assert [
        (item.source_id, item.order) for item in role.highlights
    ] == [before["Built the billing API."], before["Added checkout monitors."]]
    assert [role.order for role in layout.work_history] == [
        before["Northwind Analytics - Platform Engineer"][1]
    ]
