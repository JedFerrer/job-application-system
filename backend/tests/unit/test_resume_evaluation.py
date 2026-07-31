from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.domain.resume.schemas import ExtractionEvidence, ParsedProfile
from app.infrastructure.resume_parser import _verify_extraction_evidence
from tests.resume_evaluation import (
    EXPECTED_CASE_COUNT,
    FIELD_ORDER,
    _collection_metric,
    _finite_report,
    _metric,
    _profile_metrics,
    _warning_metric,
    assert_report_matches_case,
    evaluate_manifest,
    load_manifest,
    normalize_string,
    normalize_url,
)

pytestmark = pytest.mark.unit


def _completion(payload: dict[str, object]) -> Callable[..., Awaitable[tuple[object, str]]]:
    async def complete(**_kwargs: object) -> tuple[object, str]:
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
        ), "locked-test-model"
    return complete


def test_manifest_has_six_cases_and_all_required_contracts() -> None:
    cases = load_manifest()
    assert len(cases) == EXPECTED_CASE_COUNT
    assert [case.id for case in cases] == [
        "chronological-multiple-roles", "projects-and-links", "education-and-certifications",
        "unusual-headings", "completeness-warnings", "evidence-rejection",
    ]
    assert all(isinstance(case.expected_profile, ParsedProfile) for case in cases)
    assert all(isinstance(case.expected_verified_evidence, ExtractionEvidence) for case in cases)
    target_fields = (
        "email", "phone", "years_experience", "skills", "preferred_roles", "seniority"
    )
    successful_matches = {field: False for field in target_fields}
    for case in cases:
        actual_payload = dict(case.locked_parser_response)
        actual_payload.pop("extraction_evidence", None)
        actual_profile = ParsedProfile.model_validate(actual_payload)
        for metric in _profile_metrics(case.expected_profile, actual_profile):
            field = metric["field"]
            if (
                field in successful_matches
                and metric["tp"] > 0
                and metric["fp"] == 0
                and metric["fn"] == 0
            ):
                successful_matches[field] = True
    assert all(successful_matches.values()), successful_matches


@pytest.mark.parametrize("mutate", [
    lambda root: root.pop("provenance"),
    lambda root: root.__setitem__("schema_version", "unknown"),
    lambda root: root["cases"][1].__setitem__("id", root["cases"][0]["id"]),
    lambda root: root["cases"][0].__setitem__(
        "expected_profile", {"years_experience": {"bad": 1}}
    ),
    lambda root: root["cases"][0].__setitem__(
        "expected_verified_evidence", {"work_history": "bad"}
    ),
    lambda root: root["cases"][0].__setitem__("expected_warning_codes", "bad"),
    lambda root: root["cases"][0].__setitem__("locked_parser_response", []),
])
def test_manifest_rejects_malformed_contracts(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], object]
) -> None:
    source = json.loads(
        Path(__file__).parents[1]
        .joinpath("fixtures/resume_profile_evaluation.json")
        .read_text()
    )
    mutate(source)
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ValueError, match="resume evaluation fixture"):
        load_manifest(path)


@pytest.mark.parametrize("mutate, expected", [
    (lambda root: root["cases"][0]["expected_profile"].__setitem__(
        "unknown_field", True
    ), "unknown expected_profile"),
    (lambda root: root["cases"][0]["expected_profile"].__setitem__(
        "work_history", [{"company": "X", "unknown": True}]
    ), r"unknown expected_profile work_history\[0\] field"),
    (lambda root: root["cases"][0]["expected_profile"].__setitem__(
        "projects", [{"name": "X", "unknown": True}]
    ), r"unknown expected_profile projects\[0\] field"),
    (lambda root: root["cases"][0]["expected_verified_evidence"].__setitem__(
        "skills", []
    ), "unknown expected evidence category"),
    (lambda root: root["cases"][1]["expected_verified_evidence"].__setitem__(
        "projects", [{"item_index": 0, "excerpt": "x", "unknown": True}]
    ), r"unknown expected evidence projects\[0\] field"),
])
def test_manifest_rejects_unknown_expected_fields(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], object], expected: str
) -> None:
    source = json.loads(
        Path(__file__).parents[1]
        .joinpath("fixtures/resume_profile_evaluation.json")
        .read_text()
    )
    mutate(source)
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ValueError, match=expected):
        load_manifest(path)


def test_manifest_rejects_malformed_json_and_missing_required_field(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed JSON"):
        load_manifest(malformed)

    source = json.loads(
        Path(__file__).parents[1]
        .joinpath("fixtures/resume_profile_evaluation.json")
        .read_text()
    )
    source["cases"][0].pop("raw_text")
    missing = tmp_path / "missing.json"
    missing.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ValueError, match=r"case\[0\].*missing required fields"):
        load_manifest(missing)

    root_missing = json.loads(
        Path(__file__).parents[1]
        .joinpath("fixtures/resume_profile_evaluation.json")
        .read_text()
    )
    root_missing.pop("cases")
    root_path = tmp_path / "missing-root-field.json"
    root_path.write_text(json.dumps(root_missing), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly 6 cases are required"):
        load_manifest(root_path)


def test_normalization_handles_unicode_case_whitespace_and_urls() -> None:
    assert normalize_string("  Caf\u0065\u0301\n  Tools ") == "caf\u00e9 tools"
    assert normalize_url("HTTPS://WWW.Fictional.dev/path/?q=1#fragment") == "https://fictional.dev/path?q=1"


def test_scalar_metrics_cover_exact_missing_extra_and_empty_semantics() -> None:
    assert _metric("A", "a", "headline")["tp"] == 1
    assert _metric("A", None, "headline")["fn"] == 1
    assert _metric(None, "A", "headline")["fp"] == 1
    empty = _metric(None, None, "headline")
    assert empty["exact_match"] is True and empty["precision"] == empty["recall"] == 1.0


def test_warning_metrics_use_sets_and_reject_duplicate_expected_codes(tmp_path: Path) -> None:
    metric = _warning_metric(("missing_education",), ("missing_education", "missing_education"))
    assert (metric["tp"], metric["fp"], metric["fn"]) == (1, 0, 0)

    source = json.loads(
        Path(__file__).parents[1]
        .joinpath("fixtures/resume_profile_evaluation.json")
        .read_text()
    )
    source["cases"][0]["expected_warning_codes"] = ["missing_education", "missing_education"]
    path = tmp_path / "duplicate-warning.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(
        ValueError,
        match=r"chronological-multiple-roles.*duplicate warning code.*missing_education",
    ):
        load_manifest(path)


def test_collection_and_nested_objects_are_unordered_and_report_tp_fp_fn() -> None:
    metric = _collection_metric(
        "projects", [{"name": "A"}, {"name": "B"}], [{"name": "B"}, {"name": "C"}]
    )
    assert (metric["tp"], metric["fp"], metric["fn"]) == (1, 1, 1)


def test_project_url_normalization_preserves_paths_and_queries() -> None:
    equivalent = _collection_metric(
        "projects",
        [{"name": "A", "url": "https://fictional.dev/repo/?q=1#old"}],
        [{"name": "A", "url": "HTTPS://WWW.FICTIONAL.DEV/repo?q=1#new"}],
    )
    assert (equivalent["tp"], equivalent["fp"], equivalent["fn"]) == (1, 0, 0)
    different_path = _collection_metric(
        "projects",
        [{"name": "A", "url": "https://fictional.dev/repo-one"}],
        [{"name": "A", "url": "https://fictional.dev/repo-two"}],
    )
    different_query = _collection_metric(
        "projects",
        [{"name": "A", "url": "https://fictional.dev/repo?q=1"}],
        [{"name": "A", "url": "https://fictional.dev/repo?q=2"}],
    )
    assert (different_path["tp"], different_query["tp"]) == (0, 0)


@pytest.mark.asyncio
async def test_locked_parser_execution_is_offline_and_report_is_deterministic() -> None:
    def factory(case: Any) -> Callable[..., Awaitable[tuple[object, str]]]:
        return _completion(case.locked_parser_response)
    report_a = await evaluate_manifest(factory)
    report_b = await evaluate_manifest(factory)
    assert report_a == report_b
    assert _finite_report(report_a)
    assert report_a["schema_version"] == "resume-profile-eval-v1"
    fields = tuple(item["field"] for item in report_a["cases"][0]["profile_metrics"])
    assert fields == tuple(
        field if field != "links" else "links.linkedin"
        for field in FIELD_ORDER
        for field in (("links.linkedin", "links.github", "links.portfolio", "links.website")
                      if field == "links" else (field,))
    )


@pytest.mark.asyncio
async def test_each_case_uses_only_the_supplied_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def unexpected_provider(**_kwargs: object) -> tuple[object, str]:
        raise AssertionError("live provider path reached")

    monkeypatch.setattr("app.infrastructure.resume_parser.llm_chat_completion", unexpected_provider)

    def factory(case: Any) -> Callable[..., Awaitable[tuple[object, str]]]:
        async def complete(**_kwargs: object) -> tuple[object, str]:
            calls.append(case.id)
            return await _completion(case.locked_parser_response)(**_kwargs)
        return complete

    await evaluate_manifest(factory)
    assert calls == [case.id for case in load_manifest()]


@pytest.mark.asyncio
async def test_bypassing_supplied_completion_fails() -> None:
    async def rejected(**_kwargs: object) -> tuple[object, str]:
        raise AssertionError("locked completion was not used")

    with pytest.raises(AssertionError, match="locked completion was not used"):
        await evaluate_manifest(lambda case: rejected)


@pytest.mark.asyncio
async def test_each_case_matches_expected_contract() -> None:
    report = await evaluate_manifest(
        lambda current: _completion(current.locked_parser_response)
    )
    for case in load_manifest():
        case_report = next(item for item in report["cases"] if item["case_id"] == case.id)
        assert_report_matches_case(case_report, case)


def test_evidence_contract_contains_expected_rejections() -> None:
    case = load_manifest()[-1]
    assert len(case.expected_verified_evidence.work_history) == 1
    assert len(case.locked_parser_response["extraction_evidence"]["work_history"]) == 4
    assert (
        case.locked_parser_response["extraction_evidence"]["projects"][0]["excerpt"]
        == "not in source"
    )


def test_production_evidence_verifier_filters_invalid_items_and_preserves_facts() -> None:
    profile = ParsedProfile(
        work_history=[{"company": "Fictional Co"}, {"company": "Second Co"}],
        education=[{"degree": "Fictional Degree"}],
    )
    source = "Fictional Co Second Co Fictional Degree verified excerpt"
    evidence = _verify_extraction_evidence(
        {
            "work_history": [
                {"item_index": 0, "excerpt": "verified excerpt"},
                {"item_index": 0, "excerpt": "Second Co"},
                {"item_index": 1, "excerpt": "Second Co"},
                {"item_index": "1", "excerpt": "Second Co"},
                {"item_index": 1, "excerpt": ""},
                {"item_index": 1, "excerpt": "x" * 281},
            ],
            "education": [{"item_index": 0, "excerpt": "Fictional Degree"}],
        },
        source,
        profile,
    )
    assert [item.model_dump() for item in evidence.work_history] == [
        {"item_index": 0, "excerpt": "verified excerpt"},
        {"item_index": 1, "excerpt": "Second Co"},
    ]
    assert [item.model_dump() for item in evidence.education] == [
        {"item_index": 0, "excerpt": "Fictional Degree"}
    ]
    assert profile.work_history[0].company == "Fictional Co"
    assert profile.education[0].degree == "Fictional Degree"


def test_mismatch_diagnostics_include_case_field_expected_and_actual() -> None:
    case = load_manifest()[0]
    report = {
        "profile_metrics": [{
            "field": "headline", "expected": "expected", "actual": "actual",
            "fp": 1, "fn": 1,
        }],
        "warning_metrics": {"fp": 0, "fn": 0},
        "evidence_metrics": {"fp": 0, "fn": 0},
    }
    with pytest.raises(
        AssertionError,
        match=r"chronological-multiple-roles.*headline.*expected.*actual",
    ):
        assert_report_matches_case(report, case)
