from __future__ import annotations

import json

import pytest

from tests import tailoring_evaluation as evaluation

pytestmark = pytest.mark.unit


def test_manifest_has_exactly_seven_synthetic_cases() -> None:
    cases = evaluation.load_manifest()
    assert len(cases) == 8
    assert "synthetic" in evaluation.FIXTURE_PATH.read_text(encoding="utf-8").casefold()


def test_offline_evaluation_is_deterministic_and_credential_free() -> None:
    first = evaluation.evaluate_offline()
    second = evaluation.evaluate_offline()
    assert first == second
    assert all(item["passed"] for item in first)
    assert all("Built REST APIs with FastAPI." not in json.dumps(item) for item in first)


def test_safety_and_fallback_cases_are_reported() -> None:
    reports = {item["scenario"]: item for item in evaluation.evaluate_offline()}
    assert reports["unsupported-technology-rejected"]["selectivity"]["accepted_rewrites"] == 0
    assert reports["metrics-leadership-cross-role-rejected"]["selectivity"]["rejected_patches"] == 3
    assert reports["malformed-fallback"]["selectivity"]["fallback"] is True
    assert reports["unicode-unsafe-substitution"]["selectivity"]["accepted_rewrites"] == 0
    fallback = reports["malformed-fallback"]
    assert fallback["selectivity"]["fallback"] is True
    assert fallback["source_order"] == [1, 2, 0]


def test_uuid_reorder_and_duplicate_identity_are_exercised() -> None:
    report = {
        item["scenario"]: item for item in evaluation.evaluate_offline()
    }["duplicate-literal-uuid-reorder"]
    assert report["passed"] is True
    assert report["source_order"] == [0, 1]
    assert len(report["source_uuid_order"]) == 2
    assert len(set(report["source_uuid_order"])) == 2


def test_nontrivial_uuid_reorder_has_static_projection_and_pairing_proof() -> None:
    report = {
        item["scenario"]: item for item in evaluation.evaluate_offline()
    }["strong-match-selective-rewrite"]
    assert report["source_uuid_order"] == [
        "88d43517-9e7a-57ec-9c1e-e61181fa28d5",
        "dd223476-9337-53b8-bddd-eeccfd3bc785",
        "8fe6a8ef-257b-529f-8d4d-d22f0a4a0fb6",
    ]
    assert report["source_order"] == [1, 2, 0]
    assert report["passed"] is True


def test_classification_and_coverage_are_separate() -> None:
    reports = {item["scenario"]: item for item in evaluation.evaluate_offline()}
    verified = reports["verified-not-represented"]["coverage"]
    assert verified == [{"requirement": "python", "status": "not_represented"}]
    aliases = reports["aliases-and-explicit-empty-skills"]
    assert aliases["requirements"][0]["requirement"] == "aws"
    assert aliases["coverage"][0]["status"] == "not_represented"


def test_report_contains_diagnostics_without_raw_resume_text() -> None:
    report = evaluation.evaluate_offline()[0]
    assert {"selectivity", "evidence_ids", "rejection_reasons", "invariant_failures"} <= set(report)
    assert "Built REST APIs with FastAPI." not in json.dumps(report)


def test_evidence_category_mismatch_is_a_deterministic_invariant_failure() -> None:
    case = dict(evaluation.load_manifest()[0])
    case["expected"] = dict(case["expected"])
    case["expected"]["requirements"] = [
        dict(item) for item in case["expected"]["requirements"]
    ]
    case["expected"]["requirements"][0]["evidence_categories"] = []
    report = evaluation._evaluate_payload(case, case["locked_response"])
    assert "evidence categories mismatch" in report["invariant_failures"]


@pytest.mark.asyncio
async def test_live_requires_environment_and_explicit_enablement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def unexpected_provider(**_kwargs: object) -> tuple[object, str]:
        nonlocal calls
        calls += 1
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(evaluation, "llm_chat_completion", unexpected_provider)
    monkeypatch.setenv("RESUME_TAILORING_EVAL_LIVE", "1")
    with pytest.raises(RuntimeError, match="--live"):
        await evaluation.evaluate_live(enabled=False)
    monkeypatch.delenv("RESUME_TAILORING_EVAL_LIVE")
    with pytest.raises(RuntimeError, match="RESUME_TAILORING_EVAL_LIVE"):
        await evaluation.evaluate_live(enabled=True)
    assert calls == 0


def test_manifest_rejects_wrong_case_count(tmp_path) -> None:  # type: ignore[no-untyped-def]
    raw = json.loads(evaluation.FIXTURE_PATH.read_text(encoding="utf-8"))
    raw["cases"].pop()
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly 8 cases"):
        evaluation.load_manifest(path)
