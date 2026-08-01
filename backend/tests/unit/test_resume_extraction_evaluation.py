from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests import resume_extraction_evaluation as evaluation

pytestmark = pytest.mark.unit


def _manifest(tmp_path: Path, mutate: Callable[[dict[str, Any]], object]) -> Path:
    raw = json.loads(evaluation.MANIFEST_PATH.read_text(encoding="utf-8"))
    mutate(raw)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_manifest_has_the_locked_corpus_shape() -> None:
    cases = evaluation.load_manifest()
    assert len(cases) == 7
    assert sum(case.format == "pdf" for case in cases) == 4
    assert sum(case.format == "docx" for case in cases) == 3
    assert "Fully synthetic" in evaluation.MANIFEST_PATH.read_text(encoding="utf-8")


def test_dense_docx_case_really_has_no_warnings() -> None:
    result = next(
        item for item in evaluation.evaluate_manifest()
        if item["case_id"] == "docx-multiple-tables"
    )
    assert result["passed"] is True
    assert result["expected_warning_codes"] == []
    assert result["actual_warning_codes"] == []


def test_sparse_and_blank_pdf_cases_remain_covered() -> None:
    results = {item["case_id"]: item for item in evaluation.evaluate_manifest()}
    assert results["pdf-sparse-pages"]["actual_warning_codes"] == [
        "low_pdf_text_coverage", "likely_scanned_pdf"
    ]
    assert results["pdf-blank-no-text"]["actual_error"] == (
        "No text could be extracted from PDF. Is this a scanned/image PDF?"
    )


def test_two_column_case_has_extractor_baseline_caveat() -> None:
    manifest = evaluation.MANIFEST_PATH.read_text(encoding="utf-8")
    assert "Pinned pdfplumber extractor order" in manifest
    assert "not asserted as human reading order" in manifest


def test_every_case_evaluates_and_repeat_runs_are_identical() -> None:
    first = evaluation.evaluate_manifest()
    second = evaluation.evaluate_manifest()
    assert first == second
    assert all(result["passed"] for result in first)


def test_provider_seam_is_never_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("LLM provider reached")

    monkeypatch.setattr("app.infrastructure.resume_parser.llm_chat_completion", fail)
    assert all(result["passed"] for result in evaluation.evaluate_manifest())


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("schema", lambda raw: raw.__setitem__("schema_version", "bad")),
        ("provenance", lambda raw: raw.pop("provenance")),
        ("required field", lambda raw: raw["cases"][0].pop("byte_size")),
        ("manifest field", lambda raw: raw.__setitem__("extra", True)),
        ("duplicate id", lambda raw: raw["cases"][1].__setitem__("id", raw["cases"][0]["id"])),
        ("format", lambda raw: raw["cases"][0].__setitem__("format", "txt")),
        ("unknown field", lambda raw: raw["cases"][0].__setitem__("extra", True)),
        ("unknown fixture", lambda raw: raw["cases"][0].__setitem__("fixture", "missing.pdf")),
        ("unknown builder", lambda raw: raw["cases"][4].__setitem__("builder", "missing")),
        (
            "duplicate warning",
            lambda raw: raw["cases"][0].__setitem__(
                "expected_warning_codes", ["likely_scanned_pdf"] * 2
            ),
        ),
        ("empty blocks", lambda raw: raw["cases"][0].__setitem__("expected_blocks", [])),
        ("expectation conflict", lambda raw: raw["cases"][0].__setitem__("expected_error", "boom")),
        ("PDF builder", lambda raw: raw["cases"][0].__setitem__("builder", "bad")),
        ("DOCX fixture", lambda raw: raw["cases"][4].__setitem__("fixture", "bad.docx")),
    ],
)
def test_manifest_rejects_invalid_contract(
    tmp_path: Path, label: str, mutate: Callable[[dict[str, Any]], object]
) -> None:
    del label
    with pytest.raises(ValueError):
        evaluation.load_manifest(_manifest(tmp_path, mutate))


def test_manifest_rejects_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed JSON"):
        evaluation.load_manifest(path)


def test_manifest_reports_size_mismatch_values(tmp_path: Path) -> None:
    def mutate(raw: dict[str, Any]) -> None:
        raw["cases"][0]["byte_size"] = 1

    expected_message = (
        "Case 'pdf-multipage' fixture 'multipage.pdf' byte size mismatch: "
        "expected 1, actual 934"
    )
    with pytest.raises(ValueError, match=re.escape(expected_message)):
        evaluation.load_manifest(_manifest(tmp_path, mutate))


def test_manifest_reports_sha256_mismatch_values(tmp_path: Path) -> None:
    def mutate(raw: dict[str, Any]) -> None:
        raw["cases"][0]["sha256"] = "0" * 64

    expected_message = (
        "Case 'pdf-multipage' fixture 'multipage.pdf' SHA-256 mismatch: "
        "expected "
        + "0" * 64
        + ", actual "
        + "532f2671203f5f18bad4ec1b625637531c9d39013586a82aa7d165565fbf5e92"
    )
    with pytest.raises(ValueError, match=re.escape(expected_message)):
        evaluation.load_manifest(_manifest(tmp_path, mutate))


def test_manifest_rejects_missing_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_root = tmp_path / "fixtures"
    fixture_root.mkdir()
    for source in evaluation.MANIFEST_PATH.parent.glob("*.pdf"):
        (fixture_root / source.name).write_bytes(source.read_bytes())
    (fixture_root / "multipage.pdf").unlink()
    monkeypatch.setattr(evaluation, "_FIXTURE_ROOT", fixture_root)
    path = _manifest(tmp_path, lambda raw: raw)
    with pytest.raises(ValueError):
        evaluation.load_manifest(path)


def test_manifest_rejects_invalid_two_column_interpretation(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path,
        lambda raw: raw["cases"][1].__setitem__("interpretation", 42),
    )
    with pytest.raises(ValueError, match="interpretation"):
        evaluation.load_manifest(path)


def test_diagnostics_identify_case_and_block() -> None:
    result = {"case_id": "demo", "expected_blocks": ["one"], "actual_blocks": ["two"]}
    assert evaluation.mismatch_diagnostic(result) == (
        "case demo block 0: expected 'one'; actual 'two'"
    )


def test_no_runtime_documents_are_checked_in() -> None:
    root = evaluation.MANIFEST_PATH.parent
    assert not list(root.glob("*.docx"))
    assert len(list(root.glob("*.pdf"))) == 4
