# ruff: noqa: E501, E702, I001
"""Focused deterministic coverage for Unit 3B.1 artifact primitives."""

from __future__ import annotations

import io
import ctypes
import os
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from docx import Document
from sqlalchemy import Table
from app.domain.applications import artifacts
from app.domain.applications.artifact_lifecycle import artifact_detail
from app.models.application_artifact import ApplicationArtifact

from app.domain.applications.artifacts import (
    ArtifactFailureCategory,
    ArtifactValidationError,
    ArtifactValidationState,
    MAX_ARTIFACT_BYTES,
    MAX_DOCX_XML_BYTES,
    MAX_DOCX_EXPANDED_BYTES,
    MAX_PDF_PAGES,
    _content_checks,
    canonical_filename,
    expected_text_sha256,
    package_expected_text,
    preflight_artifact,
    sha256_hex,
    validate_in_isolated_process,
)


def _pdf_fixture() -> bytes:
    return (Path(__file__).resolve().parents[1] / "fixtures" / "resume_extraction" / "multipage.pdf").read_bytes()


def _docx(text: str) -> bytes:
    document = Document(); document.add_paragraph(text)
    output = io.BytesIO(); document.save(output)
    return output.getvalue()


@pytest.mark.parametrize("state", ["queued", "validating", "failed", "unavailable", "validated", "warning"])
def test_artifact_detail_retry_after_follows_persisted_state(state: str) -> None:
    changed_at = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    row = ApplicationArtifact(
        id=31, user_id=4, application_id=7, package_id=12,
        document_type="resume", format="pdf", filename="resume.pdf",
        content_type="application/pdf", byte_length=9, sha256="a" * 64,
        exporter_version="resume-pdf-v1", expected_text_sha256="b" * 64,
        validation_policy_version="application-artifact-validation-v1",
        validation_state=state, failure_category=None, validation_report={},
        task_id=None, validation_state_changed_at=changed_at,
        created_at=changed_at, content=b"%PDF-1.4\n",
    )
    detail = artifact_detail(row)
    assert detail.retry_after == (changed_at + timedelta(seconds=60) if state in {"queued", "validating"} else None)
    serialized = detail.model_dump(mode="json")
    assert serialized["retry_after"] == ("2026-09-24T12:01:00Z" if state in {"queued", "validating"} else None)
    assert detail.application_id == 7


def test_artifact_detail_without_legacy_state_timestamp_has_no_retry_hint() -> None:
    row = ApplicationArtifact(
        id=31, user_id=4, application_id=7, package_id=12,
        document_type="resume", format="pdf", filename="resume.pdf",
        content_type="application/pdf", byte_length=9, sha256="a" * 64,
        exporter_version="resume-pdf-v1", expected_text_sha256="b" * 64,
        validation_policy_version="application-artifact-validation-v1",
        validation_state="queued", failure_category=None, validation_report={},
        task_id=None, validation_state_changed_at=None,
        created_at=datetime(2026, 9, 24, tzinfo=UTC), content=b"%PDF-1.4\n",
    )
    assert artifact_detail(row).retry_after is None


def _small_docx_parts(*, content_types: bytes = b"<Types/>", extra: dict[str, bytes] | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", b"<document/>")
        for name, value in (extra or {}).items():
            archive.writestr(name, value)
    return output.getvalue()


def test_sha_and_filename_are_server_deterministic() -> None:
    assert sha256_hex(b"bytes") == "277089d91c0bdf4f2e6862ba7e4a07605119431f5d13f726dd352b06f1b206a9"
    assert canonical_filename("../../Résumé!.PDF", "resume", "pdf") == "Rsum.pdf"


def test_preflight_rejects_wrong_type_and_size() -> None:
    with pytest.raises(ArtifactValidationError, match="unsupported_type"):
        preflight_artifact(content=b"not a pdf", document_type="resume", format="pdf")
    with pytest.raises(ArtifactValidationError, match="size_exceeded"):
        preflight_artifact(content=b"%PDF-" + b"x" * MAX_ARTIFACT_BYTES, document_type="resume", format="pdf")


def test_docx_preflight_rejects_bad_zip_macro_and_expansion() -> None:
    with pytest.raises(ArtifactValidationError, match="invalid_docx"):
        preflight_artifact(content=b"PK\x03\x04bad", document_type="resume", format="docx")
    macro = io.BytesIO()
    with zipfile.ZipFile(macro, "w") as archive:
        archive.writestr("[Content_Types].xml", "<types/>"); archive.writestr("word/document.xml", "<document/>"); archive.writestr("word/vbaProject.bin", b"macro")
    with pytest.raises(ArtifactValidationError, match="macro_detected"):
        preflight_artifact(content=macro.getvalue(), document_type="resume", format="docx")
    expanded = io.BytesIO()
    with zipfile.ZipFile(expanded, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<types/>"); archive.writestr("word/document.xml", "x" * (MAX_DOCX_XML_BYTES + 1))
    with pytest.raises(ArtifactValidationError, match="decompression_limit"):
        preflight_artifact(content=expanded.getvalue(), document_type="resume", format="docx")


def test_docx_non_xml_expansion_is_rejected_from_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
        archive.writestr("word/media/bomb.bin", b"0" * (MAX_DOCX_EXPANDED_BYTES + 1))
    monkeypatch.setattr(zipfile.ZipFile, "testzip", lambda self: pytest.fail("main process decompressed archive"))
    with pytest.raises(ArtifactValidationError, match="decompression_limit"):
        preflight_artifact(content=bomb.getvalue(), document_type="resume", format="docx")


def test_rels_count_in_preflight_and_worker_xml_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    rels = b"<Relationships>" + b" " * 70 + b"</Relationships>"
    payload = _small_docx_parts(extra={"word/_rels/document.xml.rels": rels})
    xml_bytes = len(b"<Types/>") + len(b"<document/>")
    assert artifacts._is_docx_xml_part("word/_rels/document.xml.rels")
    monkeypatch.setattr(artifacts, "MAX_DOCX_XML_BYTES", xml_bytes + len(rels) - 1)
    with pytest.raises(ArtifactValidationError, match="decompression_limit"):
        preflight_artifact(content=payload, document_type="resume", format="docx")
    monkeypatch.setattr(artifacts, "_preflight_docx", lambda _content: None)
    with pytest.raises(ArtifactValidationError, match="decompression_limit"):
        artifacts._inspect_docx_in_worker(payload)
    monkeypatch.setattr(artifacts, "MAX_DOCX_XML_BYTES", xml_bytes + len(rels))
    preflight_artifact(content=payload, document_type="resume", format="docx")
    artifacts._inspect_docx_in_worker(payload)


@pytest.mark.parametrize("name", ["word/media/company_macro_logo.png", "word/media/vba_chart.png"])
def test_innocent_macro_or_vba_resource_name_allowed(name: str) -> None:
    payload = _small_docx_parts(extra={name: b"image"})
    preflight_artifact(content=payload, document_type="resume", format="docx")
    artifacts._inspect_docx_in_worker(payload)


def test_known_vba_part_rejected() -> None:
    payload = _small_docx_parts(extra={"word/vbaProject.bin": b"macro"})
    with pytest.raises(ArtifactValidationError, match="macro_detected"):
        preflight_artifact(content=payload, document_type="resume", format="docx")


def test_docx_macro_content_type_rejected_in_worker() -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types><Override ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/></Types>')
        archive.writestr("word/document.xml", "<document/>")
    preflight_artifact(content=payload.getvalue(), document_type="resume", format="docx")
    with pytest.raises(ArtifactValidationError, match="macro_detected"):
        validate_in_isolated_process(content=payload.getvalue(), format="docx", expected="Ada", document_type="resume")


def test_utf16_macro_content_type_rejected_in_worker() -> None:
    types = ('<?xml version="1.0" encoding="UTF-16"?>'
             '<Types><Override ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/></Types>').encode("utf-16")
    payload = _small_docx_parts(content_types=types)
    preflight_artifact(content=payload, document_type="resume", format="docx")
    with pytest.raises(ArtifactValidationError, match="macro_detected"):
        validate_in_isolated_process(content=payload, format="docx", expected="Ada", document_type="resume")


def test_normal_utf8_content_types_accepted_in_worker() -> None:
    types = b'<Types><Override ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    payload = _small_docx_parts(content_types=types)
    preflight_artifact(content=payload, document_type="resume", format="docx")
    artifacts._inspect_docx_in_worker(payload)


def test_content_type_parser_ignores_free_text_and_rejects_doctype() -> None:
    ordinary = _small_docx_parts(content_types=b'<Types><Override ContentType="application/xml" Note="macroEnabled"/></Types>')
    artifacts._inspect_docx_in_worker(ordinary)
    hostile = _small_docx_parts(content_types=b'<!DOCTYPE Types [<!ENTITY x "macroEnabled">]><Types/>')
    with pytest.raises(ArtifactValidationError, match="invalid_docx"):
        artifacts._inspect_docx_in_worker(hostile)


def test_docx_entry_and_ratio_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _small_docx_parts(extra={"word/media/a.bin": b"a", "word/media/b.bin": b"b"})
    monkeypatch.setattr(artifacts, "MAX_DOCX_ENTRIES", 3)
    with pytest.raises(ArtifactValidationError, match="decompression_limit"):
        preflight_artifact(content=payload, document_type="resume", format="docx")
    monkeypatch.setattr(artifacts, "MAX_DOCX_ENTRIES", 512)
    compact = io.BytesIO()
    with zipfile.ZipFile(compact, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("word/document.xml", b"<document/>")
        archive.writestr("word/media/bomb.bin", b"0" * 10000)
    monkeypatch.setattr(artifacts, "MAX_DOCX_EXPANSION_RATIO", 10)
    with pytest.raises(ArtifactValidationError, match="decompression_limit"):
        preflight_artifact(content=compact.getvalue(), document_type="resume", format="docx")


def test_exact_five_mib_size_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    content = b"%PDF-" + b"0" * (MAX_ARTIFACT_BYTES - 5)
    assert preflight_artifact(content=content, document_type="resume", format="pdf").byte_length == MAX_ARTIFACT_BYTES
    monkeypatch.setattr(artifacts, "_validate_pdf", lambda *_args: pytest.fail("oversized payload parsed"))
    with pytest.raises(ArtifactValidationError, match="size_exceeded"):
        preflight_artifact(content=content + b"0", document_type="resume", format="pdf")


def test_package_expected_text_keeps_unicode_numbers_punctuation_and_order() -> None:
    package: dict[str, object] = {"resume": {"content": {"full_name": "Ada Lovelace", "skills": ["C++", "C#", ".NET", "Node.js"], "work_history": [{"bullets": ["Improved 25% in 2024"]}]}}}
    expected = package_expected_text(package, "resume")
    assert "C++\nC#\n.NET\nNode.js" in expected and expected.index("Ada") < expected.index("C++")
    assert expected_text_sha256(package, "resume") == sha256_hex(expected.encode("utf-8"))


def test_resume_manifest_only_selected_rendered_content() -> None:
    package: dict[str, object] = {"resume": {"content": {
        "full_name": "Ada Lovelace", "professional_summary": "Delivered 25%",
        "missing_keywords": ["Draft 10%"], "summary_evidence": [{"excerpt": "Draft 20%"}],
        "work_history": [{"title": "Engineer", "company": "Acme", "tailored_highlights": ["Draft 10%"],
                          "selection_decisions": [{"source_index": 0, "final_included": True, "final_text": "Delivered 25%", "original_text": "Draft 10%", "suggested_text": "Draft 20%", "output_rank": 0}]}],
    }}}
    expected = package_expected_text(package, "resume")
    assert "Delivered 25%" in expected
    assert "Draft 10%" not in expected and "Draft 20%" not in expected


def test_canonical_manifest_respects_render_order_and_excludes_metadata() -> None:
    package: dict[str, object] = {"resume": {"content": {
        "tailoring_snapshot_version": "full-context-v1", "professional_summary": "Delivered 25%",
        "summary_evidence_ids": ["e1"], "missing_keywords": ["Draft 10%"],
        "source_section_order": ["contact", "summary", "skills"],
        "canonical_sections": [
            {"section": "skills", "canonical_order": 2, "items": [{"source_id": "skills", "canonical_order": 2, "text": "Draft 20%"}]},
            {"section": "contact", "canonical_order": 0, "items": [{"source_id": "contact", "canonical_order": 0, "text": "Ada"}]},
            {"section": "summary", "canonical_order": 1, "items": [{"source_id": "summary", "canonical_order": 1, "text": "Draft 10%"}]},
        ], "skills": ["C++"], "work_history": [],
    }}}
    expected = package_expected_text(package, "resume")
    assert expected == "Ada\nDelivered 25%\nC++"


def test_cover_letter_manifest_uses_only_selected_content() -> None:
    package: dict[str, object] = {"cover_letter": {"mode": "selected", "content": "Dear Ada,\nDelivered 25%", "reviewer_notes": "Draft 20%"}}
    assert package_expected_text(package, "cover_letter") == "Dear Ada, Delivered 25%"


@pytest.mark.parametrize(("actual", "expected", "valid"), [
    ("A B A", "A\nB\nA", True), ("A B", "A\nB\nA", False),
    ("Ada", "Ada\nAd", False), ("Ada ... Ad", "Ada\nAd", True),
    ("C++ C# .NET Node.js café 東京 25% 3.5 $100 2024", "C++\nC#\n.NET\nNode.js\ncafé\n東京\n25%\n3.5\n$100\n2024", True),
    ("25", "25%", False), ("2025", "2024", False),
    ("$1000", "$100", False), ("35", "3.5", False),
])
def test_ordered_distinct_boundary_and_numeric_matching(actual: str, expected: str, valid: bool) -> None:
    checks = _content_checks(actual, expected)
    assert all(check.status == "pass" for check in checks) is valid


def test_parser_timeout_terminates_and_reaps_child(tmp_path: Path) -> None:
    marker = tmp_path / "child.pid"
    script = f"import os,time,pathlib;pathlib.Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(30)"
    with pytest.raises(ArtifactValidationError, match="parser_timeout"):
        validate_in_isolated_process(content=b"x", format="pdf", expected="x", document_type="resume",
                                     timeout_seconds=0.5, worker_command=[sys.executable, "-c", script])
    assert marker.exists()
    if os.name == "nt":
        from ctypes import wintypes

        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(marker.read_text()))
        if process:
            status = wintypes.DWORD()
            assert ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(status))
            ctypes.windll.kernel32.CloseHandle(process)
            assert status.value != 259
    else:
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)


def test_parser_child_crash_has_bounded_error() -> None:
    with pytest.raises(ArtifactValidationError, match="parser_unavailable") as error:
        validate_in_isolated_process(content=b"SECRET_BINARY", format="pdf", expected="SECRET_TEXT", document_type="resume",
                                     worker_command=[sys.executable, "-c", "raise RuntimeError('SECRET_TEXT')"])
    assert "SECRET" not in str(error.value)


def test_content_checks_detect_missing_numeric_order_and_occurrence_changes() -> None:
    checks = _content_checks("C++ Improved 24% Ada", "Ada\nC++\nImproved 25%\nAda")
    failed = {check.name for check in checks if check.status == "fail"}
    assert {"required_content", "numeric_tokens", "content_order", "occurrence_counts"} <= failed


def test_isolated_pdf_and_docx_validation_report_honest_layout() -> None:
    pdf = _pdf_fixture()
    preflight_artifact(content=pdf, document_type="resume", format="pdf")
    pdf_report = validate_in_isolated_process(content=pdf, format="pdf", expected="Fictional Atlas", document_type="resume")
    assert pdf_report.state == ArtifactValidationState.VALIDATED and pdf_report.page_count == 2
    docx = _docx("Ada C++ improved 25%")
    preflight_artifact(content=docx, document_type="resume", format="docx")
    docx_report = validate_in_isolated_process(content=docx, format="docx", expected="Ada C++ improved 25%", document_type="resume")
    assert any(check.name == "pagination_layout" and check.status == "not_checked" for check in docx_report.checks)


def test_pdf_page_limit_and_blank_pages_are_safe_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    class Page:
        def extract_text(self) -> str:
            return "Ada"
    class Pdf:
        is_encrypted = False
        pages = [Page()] * (MAX_PDF_PAGES + 1)
        def __enter__(self) -> Pdf: return self
        def __exit__(self, *_args: object) -> None: pass
    monkeypatch.setattr(artifacts.pdfplumber, "open", lambda *_args: Pdf())  # type: ignore[attr-defined]
    with pytest.raises(ArtifactValidationError) as error:
        artifacts._validate_pdf(b"%PDF-", "Ada", "resume")
    assert error.value.category == ArtifactFailureCategory.PAGE_LIMIT


def test_artifact_model_declares_migration_constraints() -> None:
    table = cast(Table, ApplicationArtifact.__table__)
    names = {constraint.name for constraint in table.constraints}
    assert {
        "uq_application_artifact_identity",
        "ck_application_artifact_document_type",
        "ck_application_artifact_format",
        "ck_application_artifact_validation_state",
        "ck_application_artifact_byte_length",
    } <= names
