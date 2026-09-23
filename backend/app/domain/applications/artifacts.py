# ruff: noqa: E501, A002, I001
"""Safe, deterministic validation primitives for immutable package artifacts.

This module deliberately has no HTTP, task-store, or renderer dependency.  A
later phase owns persistence lifecycle and dispatch; this phase owns the
untrusted-byte boundary and package-derived comparison semantics.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import unicodedata
import zipfile
import base64
from xml.parsers import expat
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

import pdfplumber
from docx import Document as DocxDocument
from pydantic import BaseModel, ConfigDict, Field

MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
MAX_APPLICATION_ARTIFACT_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 50
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024
# A browser-generated resume may contain images, but its whole OOXML package
# must remain small enough to expand safely inside the isolated worker.
MAX_DOCX_EXPANDED_BYTES = 40 * 1024 * 1024
MAX_DOCX_ENTRIES = 512
MAX_DOCX_EXPANSION_RATIO = 1000
VALIDATION_POLICY_VERSION = "application-artifact-validation-v1"
PARSER_TIMEOUT_SECONDS = 10

ArtifactDocumentType = Literal["resume", "cover_letter"]
ArtifactFormat = Literal["pdf", "docx"]


class ArtifactFailureCategory(StrEnum):
    UNSUPPORTED_TYPE = "unsupported_type"
    SIZE_EXCEEDED = "size_exceeded"
    CORRUPT_PDF = "corrupt_pdf"
    ENCRYPTED_PDF = "encrypted_pdf"
    PAGE_LIMIT = "page_limit"
    INVALID_DOCX = "invalid_docx"
    MACRO_DETECTED = "macro_detected"
    DECOMPRESSION_LIMIT = "decompression_limit"
    CONTENT_MISMATCH = "content_mismatch"
    PARSER_UNAVAILABLE = "parser_unavailable"
    PARSER_TIMEOUT = "parser_timeout"
    QUOTA_EXCEEDED = "quota_exceeded"


class ArtifactValidationState(StrEnum):
    QUEUED = "queued"
    VALIDATING = "validating"
    VALIDATED = "validated"
    WARNING = "warning"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class ArtifactValidationError(ValueError):
    def __init__(self, category: ArtifactFailureCategory):
        super().__init__(category.value)
        self.category = category


class ArtifactCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    status: Literal["pass", "warning", "fail", "not_checked", "unavailable"]
    category: ArtifactFailureCategory | None = None


class ArtifactValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_version: str
    format: ArtifactFormat
    state: ArtifactValidationState
    checks: list[ArtifactCheck] = Field(max_length=20)
    page_count: int | None = Field(default=None, ge=0)
    failure_category: ArtifactFailureCategory | None = None


@dataclass(frozen=True)
class ArtifactPreflight:
    document_type: ArtifactDocumentType
    format: ArtifactFormat
    content_type: str
    byte_length: int
    sha256: str


def _is_docx_xml_part(name: str) -> bool:
    return name.casefold().endswith((".xml", ".rels"))


def _is_docx_macro_part(name: str) -> bool:
    return name.casefold() in {
        "word/vbaproject.bin",
        "word/vbadata.xml",
        "word/vbaprojectsignature.bin",
    }


def _has_macro_content_type(content: bytes) -> bool:
    macro_found = False
    parser = expat.ParserCreate(namespace_separator="}")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    def reject_doctype(_name: str, _system_id: str | None, _public_id: str | None, _has_internal_subset: bool) -> None:
        raise ArtifactValidationError(ArtifactFailureCategory.INVALID_DOCX)

    def reject_external_entity(_context: str, _base: str | None, _system_id: str | None, _public_id: str | None) -> int:
        raise ArtifactValidationError(ArtifactFailureCategory.INVALID_DOCX)

    def inspect_element(name: str, attributes: dict[str, str]) -> None:
        nonlocal macro_found
        if name.rsplit("}", 1)[-1] not in {"Default", "Override"}:
            return
        value = attributes.get("ContentType", "").casefold()
        macro_found |= "macroenabled" in value or "vbaproject" in value

    parser.StartDoctypeDeclHandler = reject_doctype
    parser.ExternalEntityRefHandler = reject_external_entity
    parser.StartElementHandler = inspect_element
    try:
        parser.Parse(content, True)
    except expat.ExpatError:
        raise ArtifactValidationError(ArtifactFailureCategory.INVALID_DOCX) from None
    return macro_found


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical_filename(filename: str, document_type: ArtifactDocumentType, format: ArtifactFormat) -> str:
    stem = re.sub(r"[^A-Za-z0-9 _.-]", "", filename.rsplit(".", 1)[0]).strip(" ._")
    return f"{stem or document_type}.{format}"


def preflight_artifact(*, content: bytes, document_type: ArtifactDocumentType, format: ArtifactFormat) -> ArtifactPreflight:
    if document_type not in ("resume", "cover_letter") or format not in ("pdf", "docx"):
        raise ArtifactValidationError(ArtifactFailureCategory.UNSUPPORTED_TYPE)
    if not content or len(content) > MAX_ARTIFACT_BYTES:
        raise ArtifactValidationError(ArtifactFailureCategory.SIZE_EXCEEDED)
    if format == "pdf":
        if not content.lstrip().startswith(b"%PDF-"):
            raise ArtifactValidationError(ArtifactFailureCategory.UNSUPPORTED_TYPE)
        content_type = "application/pdf"
    else:
        _preflight_docx(content)
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return ArtifactPreflight(document_type, format, content_type, len(content), sha256_hex(content))


def _preflight_docx(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_DOCX_ENTRIES:
                raise ArtifactValidationError(ArtifactFailureCategory.DECOMPRESSION_LIMIT)
            names = {info.filename for info in infos}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise ArtifactValidationError(ArtifactFailureCategory.INVALID_DOCX)
            if len(names) != len(infos) or any(info.is_dir() or info.flag_bits & 1 for info in infos):
                raise ArtifactValidationError(ArtifactFailureCategory.INVALID_DOCX)
            if any(_is_docx_macro_part(name) for name in names):
                raise ArtifactValidationError(ArtifactFailureCategory.MACRO_DETECTED)
            if any(info.file_size < 0 or info.compress_size < 0 for info in infos):
                raise ArtifactValidationError(ArtifactFailureCategory.INVALID_DOCX)
            if any(info.file_size > max(1, info.compress_size) * MAX_DOCX_EXPANSION_RATIO for info in infos):
                raise ArtifactValidationError(ArtifactFailureCategory.DECOMPRESSION_LIMIT)
            if sum(info.file_size for info in infos) > MAX_DOCX_EXPANDED_BYTES:
                raise ArtifactValidationError(ArtifactFailureCategory.DECOMPRESSION_LIMIT)
            xml_size = sum(info.file_size for info in infos if _is_docx_xml_part(info.filename))
            if xml_size > MAX_DOCX_XML_BYTES:
                raise ArtifactValidationError(ArtifactFailureCategory.DECOMPRESSION_LIMIT)
    except ArtifactValidationError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile):
        raise ArtifactValidationError(ArtifactFailureCategory.INVALID_DOCX) from None


def _inspect_docx_in_worker(content: bytes) -> None:
    """Stream every member under aggregate limits before handing ZIP to python-docx."""
    _preflight_docx(content)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            total = xml_total = 0
            for info in archive.infolist():
                with archive.open(info) as member:
                    member_bytes = 0
                    content_types = bytearray() if info.filename == "[Content_Types].xml" else None
                    while chunk := member.read(64 * 1024):
                        member_bytes += len(chunk)
                        total += len(chunk)
                        if _is_docx_xml_part(info.filename):
                            xml_total += len(chunk)
                        if (member_bytes > info.file_size or total > MAX_DOCX_EXPANDED_BYTES
                                or xml_total > MAX_DOCX_XML_BYTES):
                            raise ArtifactValidationError(ArtifactFailureCategory.DECOMPRESSION_LIMIT)
                        if content_types is not None:
                            content_types.extend(chunk)
                    if member_bytes != info.file_size:
                        raise ArtifactValidationError(ArtifactFailureCategory.INVALID_DOCX)
                    if content_types is not None and _has_macro_content_type(bytes(content_types)):
                        raise ArtifactValidationError(ArtifactFailureCategory.MACRO_DETECTED)
    except ArtifactValidationError:
        raise
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, EOFError):
        raise ArtifactValidationError(ArtifactFailureCategory.INVALID_DOCX) from None


def normalize_expected_text(value: str) -> str:
    """Normalize only rendering noise; punctuation and numeric tokens survive."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []


def _objects(value: object) -> list[dict[str, object]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _rank(value: object) -> int:
    return value if isinstance(value, int) else 0


def _render_date(value: object) -> str:
    date = _string(value)
    match = re.match(r"^(\d{4})-(\d{2})", date)
    if not match:
        return date
    month = int(match.group(2))
    label = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{label[month - 1] if 1 <= month <= 12 else match.group(2)} {match.group(1)}"


def _join_render_lines(values: list[str]) -> str:
    joined = ""
    for value in values:
        segment = value.strip()
        if not segment:
            continue
        joined = f"{joined}{segment}" if joined.endswith("-") else f"{joined} {segment}".strip()
    return joined


def _resume_manifest(data: dict[str, object]) -> list[str]:
    """Mirror the selected fields and ordering in cover-letter-export.ts."""
    canonical = _objects(data.get("canonical_sections"))
    sections = {section.get("section"): section for section in canonical}
    skills = (_strings(data.get("skills")) if data.get("tailoring_snapshot_version") == "full-context-v1" and data.get("skills") is not None
              else [_string(item.get("text")) for item in _objects(sections.get("skills", {}).get("items"))] if "skills" in sections
              else _strings(data.get("skills")) if data.get("skills") is not None
              else _strings(data.get("matched_skills")) + _strings(data.get("other_skills")))
    roles = _objects(data.get("work_history"))

    def role_lines() -> list[str]:
        lines: list[str] = []
        for role in roles:
            start = _render_date(role.get("start"))
            end = _render_date(role.get("end")) if role.get("end") else "Present"
            date = f"{start} – {end}" if start or end else ""
            header = _string(role.get("source_header")) or f"{_string(role.get('title')) or 'Role'} | {_string(role.get('company'))}{f' | {date}' if date else ''}"
            lines.append(header)
            context = _strings(role.get("role_context"))
            if context:
                lines.append(_join_render_lines(context))
            decisions = _objects(role.get("selection_decisions"))
            if decisions:
                included = [decision for decision in decisions if decision.get("final_included") is True]
                ranks = [decision.get("output_rank") for decision in included]
                if all(isinstance(rank, int) and rank >= 0 for rank in ranks) and len(set(ranks)) == len(ranks):
                    included.sort(key=lambda decision: _rank(decision.get("output_rank")))
                else:
                    mapping = role.get("tailored_highlight_source_indices")
                    if isinstance(mapping, list) and len(mapping) == len(included):
                        by_index = {decision.get("source_index"): decision for decision in included}
                        reordered = [by_index.get(index) for index in mapping]
                        if all(reordered):
                            included = [decision for decision in reordered if decision is not None]
                bullets = [_string(decision.get("final_text")) for decision in included]
            else:
                bullets = _strings(role.get("tailored_highlights"))
            if bullets and _string(role.get("responsibilities_heading")).strip():
                lines.append(_string(role.get("responsibilities_heading")))
            lines.extend(bullets)
        return lines

    if canonical:
        header_items = [item for section in canonical if section.get("section") in ("contact", "links") for item in _objects(section.get("items"))]
        header_items.sort(key=lambda item: _rank(item.get("canonical_order")))
        seen: set[object] = set()
        lines = []
        for item in header_items:
            source_id = item.get("source_id")
            if source_id not in seen:
                seen.add(source_id)
                lines.append(_string(item.get("text")))
        source_order = data.get("source_section_order")
        order = source_order if isinstance(source_order, list) else []
        render_sections: list[tuple[int, list[str]]] = []
        for section in canonical:
            kind = section.get("section")
            if kind in ("contact", "links", "unclassified") or not _objects(section.get("items")):
                continue
            items = _objects(section.get("items"))
            values = [_string(item.get("text")) for item in items]
            if kind == "summary":
                if data.get("tailoring_snapshot_version") == "full-context-v1" and _strings(data.get("summary_evidence_ids")) and _string(data.get("professional_summary")):
                    values = [_string(data.get("professional_summary"))]
                else:
                    values = [_join_render_lines(values)]
            elif kind == "skills" and data.get("tailoring_snapshot_version") == "full-context-v1":
                values = skills
            first_id = items[0].get("source_id")
            position = order.index(first_id) if first_id in order else _rank(section.get("canonical_order"))
            render_sections.append((position, values))
        if roles:
            role_orders = []
            for index, role in enumerate(roles):
                source_id = role.get("source_role_id")
                decisions = _objects(role.get("selection_decisions"))
                role_orders.append(order.index(source_id) if source_id in order else min((_rank(item.get("canonical_order")) for item in decisions), default=2**53 + index))
            render_sections.append((min(role_orders), role_lines()))
        for _, values in sorted(render_sections, key=lambda section: section[0]):
            lines.extend(values)
        return lines

    links = data.get("links")
    link_lines = [f"{key[:1].upper()}{key[1:]}: {value}" for key, value in links.items() if isinstance(value, str) and value] if isinstance(links, dict) else []
    lines = [_string(data.get(key)) for key in ("full_name", "headline", "email", "phone")] + link_lines
    lines += [_string(data.get("professional_summary"))] + skills + role_lines()
    for project in _objects(data.get("projects")):
        lines += [_string(project.get("name")), _string(project.get("description"))]
        lines += _strings(project.get("technologies")) + _strings(project.get("achievements")) + [_string(project.get("url"))]
    lines += [" | ".join(filter(None, (_string(item.get(key)) for key in ("degree", "institution", "year")))) for item in _objects(data.get("education"))]
    lines += _strings(data.get("certifications"))
    for section_name, keys in (("licenses", ("name", "issuer")), ("languages", ("language", "proficiency"))):
        lines += [" | ".join(filter(None, (_string(item.get(key)) for key in keys))) for item in _objects(data.get(section_name))]
    return lines


def package_expected_text(package_content: dict[str, object], document_type: ArtifactDocumentType) -> str:
    value = package_content.get(document_type)
    if document_type == "cover_letter":
        if not isinstance(value, dict) or value.get("mode") != "selected" or not isinstance(value.get("content"), str):
            raise ArtifactValidationError(ArtifactFailureCategory.CONTENT_MISMATCH)
        return normalize_expected_text(value["content"])
    if not isinstance(value, dict) or not isinstance(value.get("content"), dict):
        raise ArtifactValidationError(ArtifactFailureCategory.CONTENT_MISMATCH)
    return "\n".join(normalize_expected_text(item) for item in _resume_manifest(value["content"]) if normalize_expected_text(item))


def expected_text_sha256(package_content: dict[str, object], document_type: ArtifactDocumentType) -> str:
    return sha256_hex(package_expected_text(package_content, document_type).encode("utf-8"))


def _numeric_tokens(value: str) -> list[str]:
    return re.findall(r"(?<!\w)(?:\$?\d+(?:[.,]\d+)?%?|\d{4}(?:[-/]\d{1,2})?)(?!\w)", value)


def _validation_worker(content: bytes, format: ArtifactFormat, expected: str, document_type: ArtifactDocumentType) -> dict[str, object]:
    if format == "pdf":
        return _validate_pdf(content, expected, document_type).model_dump(mode="json")
    return _validate_docx(content, expected).model_dump(mode="json")


def validate_in_isolated_process(*, content: bytes, format: ArtifactFormat, expected: str, document_type: ArtifactDocumentType, timeout_seconds: float = PARSER_TIMEOUT_SECONDS, worker_command: list[str] | None = None) -> ArtifactValidationReport:
    """Use a bounded interpreter subprocess so parser hangs cannot retain request workers."""
    request = json.dumps({
        "content": base64.b64encode(content).decode("ascii"),
        "format": format,
        "expected": expected,
        "document_type": document_type,
    }).encode("utf-8")
    try:
        completed = subprocess.run(  # noqa: S603 - command is the fixed worker; override is a test seam
            worker_command or [sys.executable, "-m", "app.domain.applications.artifact_worker"],
            input=request, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired:
        raise ArtifactValidationError(ArtifactFailureCategory.PARSER_TIMEOUT) from None
    if completed.returncode != 0:
        raise ArtifactValidationError(ArtifactFailureCategory.PARSER_UNAVAILABLE) from None
    try:
        payload = json.loads(completed.stdout)
        if payload.get("failure_category"):
            raise ArtifactValidationError(ArtifactFailureCategory(payload["failure_category"]))
        return ArtifactValidationReport.model_validate(payload["report"])
    except ArtifactValidationError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ArtifactValidationError(ArtifactFailureCategory.PARSER_UNAVAILABLE) from None


def _content_checks(extracted: str, expected: str) -> list[ArtifactCheck]:
    actual = normalize_expected_text(extracted)
    expected_parts = [normalize_expected_text(part) for part in expected.split("\n") if normalize_expected_text(part)]
    def occurrence(part: str, cursor: int) -> int:
        while (position := actual.find(part, cursor)) >= 0:
            end = position + len(part)
            left = position == 0 or not _segment_character(actual[position - 1]) or not _segment_character(part[0])
            right = end == len(actual) or not _segment_character(actual[end]) or not _segment_character(part[-1])
            if left and right:
                return position
            cursor = position + 1
        return -1

    cursor = 0
    matched = 0
    for part in expected_parts:
        position = occurrence(part, cursor)
        if position < 0:
            break
        matched += 1
        cursor = position + len(part)
    ordered = matched == len(expected_parts)
    missing = any(occurrence(part, 0) < 0 for part in expected_parts)
    numeric_ok = _numeric_tokens(actual) == _numeric_tokens(" ".join(expected_parts))
    occurrence_ok = ordered
    return [
        ArtifactCheck(name="required_content", status="fail" if missing else "pass", category=ArtifactFailureCategory.CONTENT_MISMATCH if missing else None),
        ArtifactCheck(name="numeric_tokens", status="fail" if not numeric_ok else "pass", category=ArtifactFailureCategory.CONTENT_MISMATCH if not numeric_ok else None),
        ArtifactCheck(name="content_order", status="fail" if not ordered else "pass", category=ArtifactFailureCategory.CONTENT_MISMATCH if not ordered else None),
        ArtifactCheck(name="occurrence_counts", status="fail" if not occurrence_ok else "pass", category=ArtifactFailureCategory.CONTENT_MISMATCH if not occurrence_ok else None),
    ]


def _segment_character(value: str) -> bool:
    return value.isalnum() or value in "_+#.%$"


def _report(format: ArtifactFormat, checks: list[ArtifactCheck], page_count: int | None = None) -> ArtifactValidationReport:
    failed = next((check for check in checks if check.status == "fail"), None)
    warned = any(check.status == "warning" for check in checks)
    return ArtifactValidationReport(
        policy_version=VALIDATION_POLICY_VERSION, format=format,
        state=ArtifactValidationState.FAILED if failed else ArtifactValidationState.WARNING if warned else ArtifactValidationState.VALIDATED,
        checks=checks, page_count=page_count, failure_category=failed.category if failed else None,
    )


def _validate_pdf(content: bytes, expected: str, document_type: ArtifactDocumentType) -> ArtifactValidationReport:
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            if bool(getattr(pdf, "is_encrypted", False) or getattr(getattr(pdf, "doc", None), "is_encrypted", False)):
                raise ArtifactValidationError(ArtifactFailureCategory.ENCRYPTED_PDF)
            page_count = len(pdf.pages)
            if page_count > MAX_PDF_PAGES:
                raise ArtifactValidationError(ArtifactFailureCategory.PAGE_LIMIT)
            texts = [page.extract_text() or "" for page in pdf.pages]
            checks = _content_checks("\n".join(texts), expected)
            blank_page = any(not text.strip() for text in texts)
            checks.append(
                ArtifactCheck(
                    name="blank_pages", status="fail" if blank_page else "pass",
                    category=ArtifactFailureCategory.CONTENT_MISMATCH if blank_page else None,
                )
            )
            out_of_bounds = any(
                char["x0"] < 0 or char["x1"] > page.width or char["top"] < 0 or char["bottom"] > page.height
                for page in pdf.pages for char in page.chars
            )
            checks.append(
                ArtifactCheck(
                    name="text_bounds", status="fail" if out_of_bounds else "pass",
                    category=ArtifactFailureCategory.CONTENT_MISMATCH if out_of_bounds else None,
                )
            )
            threshold = 2 if document_type == "resume" else 1
            if page_count > threshold:
                checks.append(ArtifactCheck(name="page_preference", status="warning"))
            return _report("pdf", checks, page_count)
    except ArtifactValidationError:
        raise
    except Exception:
        raise ArtifactValidationError(ArtifactFailureCategory.CORRUPT_PDF) from None


def _validate_docx(content: bytes, expected: str) -> ArtifactValidationReport:
    try:
        _inspect_docx_in_worker(content)
        document = DocxDocument(io.BytesIO(content))
        blocks = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                blocks.extend(cell.text for cell in row.cells)
        checks = _content_checks("\n".join(blocks), expected)
        checks.append(ArtifactCheck(name="pagination_layout", status="not_checked"))
        return _report("docx", checks)
    except ArtifactValidationError:
        raise
    except Exception:
        raise ArtifactValidationError(ArtifactFailureCategory.INVALID_DOCX) from None
