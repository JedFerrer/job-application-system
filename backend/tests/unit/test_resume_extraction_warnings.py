from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pdfplumber
import pytest
from docx import Document

from app.domain.resume.schemas import ExtractionWarning, ParsedProfile
from app.domain.resume.service import ResumeService
from app.infrastructure import resume_parser
from app.infrastructure.resume_parser import ExtractedResumeText, extract_text_from_bytes

pytestmark = pytest.mark.unit


class _FakePdf:
    def __init__(self, pages: list[str | None]) -> None:
        self.pages = [SimpleNamespace(extract_text=lambda text=text: text) for text in pages]

    def __enter__(self) -> _FakePdf:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _WarningRepository:
    async def get_active_profile(self, user_id: int) -> None:
        del user_id
        return None

    async def lock_user_for_import(self, user_id: int) -> None:
        del user_id

    async def add_resume(self, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            id=12,
            filename=kwargs["filename"],
            is_active=kwargs["is_active"],
            lifecycle_status=getattr(kwargs["lifecycle_status"], "value", "active"),
        )

    async def add_profile(self, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(**kwargs)


def _docx_bytes(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def test_clean_docx_has_no_extraction_warnings() -> None:
    result = extract_text_from_bytes(
        _docx_bytes("A" * resume_parser.SHORT_TEXT_WARNING_THRESHOLD),
        "resume.docx",
        True,
    )

    assert isinstance(result, ExtractedResumeText)
    assert result.warnings == []


def test_short_text_warning_starts_below_threshold() -> None:
    boundary = extract_text_from_bytes(
        _docx_bytes("A" * resume_parser.SHORT_TEXT_WARNING_THRESHOLD),
        "resume.docx",
        True,
    )
    below = extract_text_from_bytes(
        _docx_bytes("A" * (resume_parser.SHORT_TEXT_WARNING_THRESHOLD - 1)),
        "resume.docx",
        True,
    )

    assert isinstance(boundary, ExtractedResumeText)
    assert isinstance(below, ExtractedResumeText)
    assert boundary.warnings == []
    assert [warning.code for warning in below.warnings] == ["short_extracted_text"]


def test_sparse_pdf_warns_about_coverage_and_scanned_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pdfplumber,
        "open",
        lambda _stream: _FakePdf(["A" * 600, "", "", ""]),
    )

    result = extract_text_from_bytes(b"pdf", "resume.pdf", True)

    assert isinstance(result, ExtractedResumeText)
    assert [warning.code for warning in result.warnings] == [
        "low_pdf_text_coverage",
        "likely_scanned_pdf",
    ]


def test_one_page_short_pdf_warns_about_coverage_and_scanned_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pdfplumber,
        "open",
        lambda _stream: _FakePdf(["A" * 20]),
    )

    result = extract_text_from_bytes(b"pdf", "resume.pdf", True)

    assert isinstance(result, ExtractedResumeText)
    warning_codes = {warning.code for warning in result.warnings}
    assert {"low_pdf_text_coverage", "likely_scanned_pdf"} <= warning_codes


def test_one_page_meaningful_pdf_has_no_pdf_coverage_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pdfplumber,
        "open",
        lambda _stream: _FakePdf(["A" * resume_parser.PDF_MEANINGFUL_PAGE_TEXT_THRESHOLD]),
    )

    result = extract_text_from_bytes(b"pdf", "resume.pdf", True)

    assert isinstance(result, ExtractedResumeText)
    warning_codes = {warning.code for warning in result.warnings}
    assert "low_pdf_text_coverage" not in warning_codes
    assert "likely_scanned_pdf" not in warning_codes


def test_empty_pdf_remains_a_fatal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pdfplumber,
        "open",
        lambda _stream: _FakePdf([None, "  "]),
    )

    with pytest.raises(ValueError, match="No text could be extracted from PDF"):
        extract_text_from_bytes(b"pdf", "resume.pdf", True)


@pytest.mark.asyncio
async def test_service_carries_warnings_without_changing_import_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warning = ExtractionWarning(
        code="short_extracted_text",
        message="Review the imported profile for missing text.",
    )
    profile = ParsedProfile(headline="Engineer")
    monkeypatch.setattr(
        "app.domain.resume.service.extract_text_from_bytes",
        lambda *_: ExtractedResumeText("text", [warning]),
    )

    async def parse(_: str) -> ParsedProfile:
        return profile

    monkeypatch.setattr("app.domain.resume.service.parse_resume_text", parse)

    async def embed(_: str) -> list[float]:
        return [0.1, 0.2]

    monkeypatch.setattr("app.domain.resume.service.embed_text", embed)

    result = await ResumeService(_WarningRepository()).upload_and_parse(  # type: ignore[arg-type]
        user_id=7,
        filename="resume.pdf",
        content=b"pdf",
    )

    assert result.extraction_warnings == [warning]
    assert result.matching_profile_changed is False
    assert result.is_active is False


@pytest.mark.asyncio
async def test_service_appends_completeness_warnings_after_extraction_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extraction_warning = ExtractionWarning(
        code="short_extracted_text",
        message="Review the imported profile for missing text.",
    )
    profile = ParsedProfile()
    monkeypatch.setattr(
        "app.domain.resume.service.extract_text_from_bytes",
        lambda *_: ExtractedResumeText(
            "ada@example.com\n\nEXPERIENCE\nBackend Engineer",
            [extraction_warning],
        ),
    )
    monkeypatch.setattr("app.domain.resume.service.parse_resume_text", lambda _: _profile(profile))
    monkeypatch.setattr("app.domain.resume.service.embed_text", lambda _: _embedding())

    result = await ResumeService(_WarningRepository()).upload_and_parse(  # type: ignore[arg-type]
        user_id=7, filename="resume.pdf", content=b"pdf"
    )

    assert [warning.code for warning in result.extraction_warnings] == [
        "short_extracted_text",
        "source_email_not_parsed",
        "missing_work_history",
    ]
    payload = result.model_dump(mode="json")
    assert [warning["code"] for warning in payload["extraction_warnings"]] == [
        "short_extracted_text",
        "source_email_not_parsed",
        "missing_work_history",
    ]


async def _profile(profile: ParsedProfile) -> ParsedProfile:
    return profile


async def _embedding() -> list[float]:
    return [0.1, 0.2]


@pytest.mark.asyncio
async def test_service_ignores_malformed_social_url_without_failing_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = ParsedProfile()
    monkeypatch.setattr(
        "app.domain.resume.service.extract_text_from_bytes",
        lambda *_: ExtractedResumeText("https://github.com:abc/path", []),
    )
    monkeypatch.setattr("app.domain.resume.service.parse_resume_text", lambda _: _profile(profile))
    monkeypatch.setattr("app.domain.resume.service.embed_text", lambda _: _embedding())

    result = await ResumeService(_WarningRepository()).upload_and_parse(  # type: ignore[arg-type]
        user_id=7, filename="resume.pdf", content=b"pdf"
    )

    assert result.extraction_warnings == []
    assert result.is_active is False
