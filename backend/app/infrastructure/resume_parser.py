# ruff: noqa: E501
"""Resume file parsing: byte extraction (PDF/DOCX) + LLM structuring.

Ported from hr-ai-nterviewer-saas (``services/resume_service.py``). File text
extraction lives here (infrastructure); the LLM call returns a validated
``ParsedProfile``.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
from collections.abc import Iterator
from typing import Any, Literal, NamedTuple, overload

import pdfplumber
from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.resume.prompts import RESUME_EXTRACTION_PROMPT
from app.domain.resume.schemas import (
    EvidenceItem,
    ExtractionEvidence,
    ExtractionWarning,
    ParsedProfile,
    ParsedResumeResult,
    SourceResumeContentDocumentV2,
    SourceResumeContentPayloadV4,
)
from app.domain.resume.source_content import (
    SourceLayoutResult,
    build_source_candidate_ledger,
    classify_source_artifacts,
    has_usable_tailoring_semantics,
    resolve_source_layout_v4,
    verify_source_candidate_ledger,
)
from app.infrastructure.llm_client import llm_chat_completion
from app.infrastructure.llm_utils import strip_code_fences

logger = get_logger("resume_parser")

RESUME_PARSER_CONFIG_ID = "resume-parser-v7"
EVIDENCE_VERIFIER_VERSION = "evidence-verifier-v3"
NATIVE_EXTRACTION_VERSION = "native-extraction-v1"

SUPPORTED_EXTENSIONS = {"pdf", "docx"}
# Defence-in-depth size cap (handlers also enforce this at the boundary).
MAX_RESUME_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_RESUME_TEXT_CHARS = 60_000
_MAX_SOURCE_CONTRACT_ATTEMPTS = 2


def _profile_alias_leak_paths(
    payload: dict[str, Any], issued_aliases: set[str]
) -> tuple[str, ...]:
    """Find request-local source aliases in normalized profile prose fields.

    Candidate aliases are valid only inside ``source_references`` (removed from
    the profile payload before this function runs). Walk every normalized
    profile scalar/list field so aliases cannot silently pass schema validation
    and reach persistence, matching, or embeddings.
    """
    paths: list[str] = []

    def visit(value: object, path: str) -> None:
        if isinstance(value, str):
            if value in issued_aliases:
                paths.append(path)
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}.{index}")
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str):
                    visit(item, f"{path}.{key}" if path else key)

    visit(payload, "")
    return tuple(paths)


def _source_contract_retry_prompt(
    base_prompt: str,
    *,
    zero_semantic: bool = False,
    error_codes: tuple[str, ...] = (),
    invalid_reasons: tuple[str, ...] = (),
    invalid_paths: tuple[str, ...] = (),
    invalid_aliases: tuple[str | None, ...] = (),
) -> str:
    prompt = base_prompt + (
        "\n\nRETRY: The previous output mixed the two contracts. Return a separate "
        "normalized CandidateProfile and semantic source_references object. "
        "Every CandidateProfile field must contain normalized human-readable "
        "values; no request-local cN alias may appear anywhere in the profile. "
        "The separate source_references object must use only exact aliases from "
        "the server-issued candidate list: contact/links maps, a summary scalar "
        "plus ordered summary_lines, section_headings (use only summary, skills, "
        "work_history, education, certifications, projects, licenses, languages, "
        "or named:<index>), skills/sections/education/"
        "certifications/licenses/languages arrays, "
        "work_history entries with header plus highlights arrays, and project "
        "name/description/url scalars plus technologies/achievements arrays. "
        "Work-history role order is derived by the server from each header and "
        "the model-provided order is ignored. Do not return source prose, UUIDs, "
        "offsets, fingerprints, or verification metadata."
    )
    if zero_semantic:
        return prompt + (
            " The prior semantic annotation contained no usable work evidence. "
            "Return at least one work_history object with one issued header alias "
            "and one issued highlight alias when the resume supports it. Partial "
            "annotation is allowed: do not invent aliases or classify every source "
            "candidate, because the server preserves unclassified content."
        )
    if error_codes:
        safe_feedback = [f"codes={','.join(error_codes)}"]
        if invalid_reasons:
            safe_feedback.append(f"reasons={','.join(invalid_reasons)}")
        if invalid_paths:
            safe_feedback.append(f"paths={','.join(invalid_paths)}")
        aliases = [alias for alias in invalid_aliases if alias is not None]
        if aliases:
            safe_feedback.append(f"aliases={','.join(aliases)}")
        return prompt + " Correct these structural source-reference errors only: " + "; ".join(safe_feedback) + "."
    return prompt


def _required_header_layout_errors(
    profile: ParsedProfile,
    layout: object,
    ledger: SourceResumeContentDocumentV2,
) -> tuple[str, ...]:
    """Fail closed when parsed identity data was not retained in source metadata."""
    contact_ids = set(getattr(layout, "contact", {}).values())
    link_ids = set(getattr(layout, "links", {}).values())
    by_id = {candidate.source_id: candidate.text for candidate in ledger.candidates}
    by_order = {candidate.order: candidate for candidate in ledger.candidates}
    assigned_metadata = contact_ids | link_ids
    contact = "\n".join(by_id[value].casefold() for value in contact_ids if value in by_id)
    links = "\n".join(by_id[value].casefold() for value in link_ids if value in by_id)
    errors: list[str] = []
    source_values = {candidate.text.casefold() for candidate in ledger.candidates}
    if profile.full_name:
        normalized_name = profile.full_name.casefold()
        if normalized_name not in source_values or normalized_name not in contact:
            errors.append("missing_source_contact_name")
    if profile.email and profile.email.casefold() not in contact:
        errors.append("missing_source_contact_email")
    if profile.phone:
        digits = re.sub(r"\D", "", profile.phone)
        if digits and digits not in re.sub(r"\D", "", contact):
            errors.append("missing_source_contact_phone")
    for value in profile.links.model_dump().values():
        if not value:
            continue
        token = re.sub(r"^https?://", "", value.casefold()).rstrip("/")
        if token and token not in links:
            errors.append("missing_source_professional_link")
            break

    # Every non-heading line before the first core section heading is header
    # material (name, address, email, phone, or links). This catches address
    # lines that have no normalized profile field of their own.
    heading_names = {
        "professional summary", "summary", "technical stack", "technical skills",
        "professional experience", "work experience", "experience", "education",
        "certifications", "projects", "languages", "licenses",
        "awards and achievements", "awards & achievements",
    }
    first_heading = next(
        (candidate.order for candidate in ledger.candidates if candidate.text.casefold().strip() in heading_names),
        None,
    )
    if first_heading is not None:
        missing_header = [
            candidate for order, candidate in by_order.items()
            if order < first_heading
            and candidate.text.casefold().strip() not in heading_names
            and candidate.source_id not in assigned_metadata
        ]
        if missing_header:
            errors.append("missing_source_header_candidates")

    # Discover headings from the server-owned ledger, rather than trusting the
    # model to include heading references. Every detected core heading must be
    # retained separately, and every candidate in its body must be classified
    # under that section.
    heading_by_name: dict[str, int] = {}
    heading_ids = getattr(layout, "section_headings", {})
    heading_key_by_name = {
        "professional summary": "summary", "summary": "summary",
        "technical stack": "skills", "technical skills": "skills",
        "professional experience": "work_history", "work experience": "work_history",
        "experience": "work_history", "education": "education",
        "certifications": "certifications", "projects": "projects",
        "languages": "languages", "licenses": "licenses",
        "awards and achievements": "named:",
        "awards & achievements": "named:",
    }
    for candidate in ledger.candidates:
        heading = candidate.text.casefold().strip()
        if heading in heading_names:
            heading_by_name.setdefault(heading, candidate.order)
            heading_key = heading_key_by_name[heading]
            if heading_key == "named:":
                heading_key = next(
                    (
                        key
                        for key, value in heading_ids.items()
                        if key.startswith("named:") and value == candidate.source_id
                    ),
                    "named:-1",
                )
                heading_key_by_name[heading] = heading_key
            if heading_key == "named:-1" or heading_ids.get(heading_key) != candidate.source_id:
                errors.append("missing_source_section_heading")
    summary_order = next(
        (heading_by_name[name] for name in ("professional summary", "summary") if name in heading_by_name),
        None,
    )
    skills_order = next(
        (heading_by_name[name] for name in ("technical stack", "technical skills") if name in heading_by_name),
        None,
    )
    summary_ids = {
        value
        for value in [getattr(layout, "summary", None), *getattr(layout, "summary_lines", [])]
        if value is not None
    }
    if summary_order is not None and skills_order is not None and summary_order < skills_order:
        if getattr(layout, "summary", None) is None:
            errors.append("missing_source_summary")
        missing_summary = [
            candidate
            for order, candidate in by_order.items()
            if summary_order < order < skills_order and candidate.source_id not in summary_ids
        ]
        if missing_summary:
            errors.append("incomplete_source_summary")

    section_ids: dict[str, set[object]] = {
        "summary": {
            value
            for value in [getattr(layout, "summary", None), *getattr(layout, "summary_lines", [])]
            if value is not None
        },
        "skills": set(getattr(layout, "skills", [])),
        "education": set(getattr(layout, "education", [])),
        "certifications": set(getattr(layout, "certifications", [])),
        "languages": set(getattr(layout, "languages", [])),
        "licenses": set(getattr(layout, "licenses", [])),
        "projects": {
            value
            for project in getattr(layout, "projects", [])
            for value in (
                project.name,
                project.description,
                *project.technologies,
                *project.achievements,
                project.url,
            )
            if value is not None
        },
    }
    work_ids: set[object] = set()
    for role in getattr(layout, "work_history", []):
        work_ids.update(
            value
            for value in (
                getattr(role, "header", None),
                *getattr(role, "context", []),
                *getattr(role, "highlights", []),
                getattr(role, "responsibilities_heading", None),
            )
            if value is not None
        )
    section_ids["work_history"] = work_ids
    named_sections = getattr(layout, "named_sections", [])
    for index, named_section in enumerate(named_sections):
        section_ids[f"named:{index}"] = {
            value
            for value in [named_section.heading, *named_section.items]
            if value is not None
        }
    recognized_orders = sorted(heading_by_name.values())
    for heading_name, heading_order in heading_by_name.items():
        section_key = heading_key_by_name[heading_name]
        next_heading_order = next(
            (order for order in recognized_orders if order > heading_order),
            len(ledger.candidates),
        )
        missing_body = [
            candidate
            for order, candidate in by_order.items()
            if heading_order < order < next_heading_order
            and candidate.source_id not in section_ids.get(section_key, set())
        ]
        if missing_body:
            errors.append("unclassified_core_section_content")
    return tuple(errors)


def _normalize_profile_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep normalized profile highlights as strings when the model mirrors source items.

    Source content uses ``{"text", "order"}`` items, while ParsedProfile uses
    plain highlight strings. This narrow adapter separates the two contracts
    without accepting arbitrary objects or changing canonical source data.
    """
    work_history = payload.get("work_history")
    if not isinstance(work_history, list):
        return payload
    normalized_roles: list[Any] = []
    for role in work_history:
        if not isinstance(role, dict):
            normalized_roles.append(role)
            continue
        normalized_role = dict(role)
        highlights = normalized_role.get("highlights")
        if isinstance(highlights, list):
            normalized_role["highlights"] = [
                item["text"]
                if isinstance(item, dict)
                and isinstance(item.get("text"), str)
                and set(item) == {"text", "order"}
                and type(item.get("order")) is int
                else item
                for item in highlights
            ]
        normalized_roles.append(normalized_role)
    result = dict(payload)
    result["work_history"] = normalized_roles
    return result


def _validation_log_fields(exc: ValidationError) -> dict[str, object]:
    """Return structural validation metadata without values from the resume."""
    errors = exc.errors()
    return {
        "error_count": len(errors),
        "error_types": tuple(
            str(error.get("type", "unknown")) for error in errors
        ),
        "error_paths": tuple(
            ".".join(str(part) for part in error.get("loc", ()))
            for error in errors
        ),
    }
SHORT_TEXT_WARNING_THRESHOLD = 600
PDF_MEANINGFUL_PAGE_TEXT_THRESHOLD = 40
PDF_LOW_COVERAGE_THRESHOLD = 0.5


class ExtractedResumeText(NamedTuple):
    text: str
    warnings: list[ExtractionWarning]


@overload
def extract_text_from_bytes(
    content: bytes, filename: str, include_warnings: Literal[False] = False
) -> str: ...


@overload
def extract_text_from_bytes(
    content: bytes, filename: str, include_warnings: Literal[True]
) -> ExtractedResumeText: ...


def extract_text_from_bytes(
    content: bytes, filename: str, include_warnings: bool = False
) -> str | ExtractedResumeText:
    """Extract text from resume bytes. Supports PDF and DOCX.

    Raises ``ValueError`` if the format is unsupported or no text is extracted.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "pdf":
        result = _extract_pdf(content)
    elif ext == "docx":
        result = _extract_docx(content)
    else:
        raise ValueError(f"Unsupported file type '.{ext}'. Accepted: PDF, DOCX")
    return result if include_warnings else result.text


def _extract_pdf(content: bytes) -> ExtractedResumeText:
    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    if not any(page.strip() for page in pages):
        raise ValueError("No text could be extracted from PDF. Is this a scanned/image PDF?")
    text = "\n\n".join(page for page in pages if page.strip())
    meaningful_pages = sum(
        len(page.strip()) >= PDF_MEANINGFUL_PAGE_TEXT_THRESHOLD for page in pages
    )
    warnings = _text_warnings(text)
    if pages:
        coverage = meaningful_pages / len(pages)
        if coverage < PDF_LOW_COVERAGE_THRESHOLD:
            warnings.append(
                ExtractionWarning(
                    code="low_pdf_text_coverage",
                    message=(
                        "Text was extracted from only some PDF pages. Review the "
                        "imported profile for missing sections."
                    ),
                )
            )
        if (len(pages) - meaningful_pages) / len(pages) >= PDF_LOW_COVERAGE_THRESHOLD:
            warnings.append(
                ExtractionWarning(
                    code="likely_scanned_pdf",
                    message=(
                        "This PDF appears to contain scanned or image-based pages. "
                        "OCR is not available; review the imported profile for missing text."
                    ),
                )
            )
    return ExtractedResumeText(text=text, warnings=warnings)


def _extract_docx(content: bytes) -> ExtractedResumeText:
    document = DocxDocument(io.BytesIO(content))
    blocks: list[str] = []
    for block in _iter_docx_blocks(document):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text:
                blocks.append(text)
            continue

        for row in block.rows:
            cells = []
            for cell in row.cells:
                cell_text = " ".join(
                    paragraph.text.strip()
                    for paragraph in cell.paragraphs
                    if paragraph.text.strip()
                )
                if cell_text:
                    cells.append(cell_text)
            if cells:
                blocks.append(" | ".join(cells))

    if not blocks:
        raise ValueError("No text could be extracted from DOCX. Is the document empty?")
    text = "\n\n".join(blocks)
    return ExtractedResumeText(text=text, warnings=_text_warnings(text))


def _text_warnings(text: str) -> list[ExtractionWarning]:
    if not text or len(text) >= SHORT_TEXT_WARNING_THRESHOLD:
        return []
    return [
        ExtractionWarning(
            code="short_extracted_text",
            message=(
                "Only a small amount of text was extracted. Review the imported "
                "profile before using it."
            ),
        )
    ]


def _iter_docx_blocks(document: Any) -> Iterator[Paragraph | Table]:
    """Yield top-level paragraphs and tables in their source order."""
    body = document.element.body
    for element in body.iterchildren():
        if element.tag.endswith("}p"):
            yield Paragraph(element, document)
        elif element.tag.endswith("}tbl"):
            yield Table(element, document)


async def parse_resume_text(resume_text: str) -> ParsedResumeResult:
    """Send resume text to the LLM and return profile plus verified evidence.

    Raises ``ValueError`` when the LLM output is not valid JSON or fails schema
    validation (normalized so callers handle a single exception type).
    """
    ledger = build_source_candidate_ledger(resume_text)
    ledger_errors = verify_source_candidate_ledger(ledger, resume_text)
    if ledger_errors:
        raise ValueError("Server source candidate ledger failed verification")
    candidate_prompt = json.dumps(
        [
            {"candidate_alias": f"c{candidate.order}", "text": candidate.text}
            for candidate in ledger.candidates
        ],
        ensure_ascii=False,
    )
    issued_aliases = {f"c{candidate.order}" for candidate in ledger.candidates}
    qa_payload = _qa_fixture_payload(resume_text, ledger)
    base_prompt = (
        RESUME_EXTRACTION_PROMPT.format(resume_text=resume_text)
        + "\n\nSERVER-OWNED SOURCE CANDIDATES:\n"
        + candidate_prompt
        + "\n\nReturn semantic source_references using only candidate_alias values from this list. "
        "Never return source prose, UUIDs, offsets, fingerprints, occurrence ordinals, "
        "or verification status. For each work-history role use one `header` alias "
        "for the complete raw role header and `highlights` aliases; do not split "
        "company/title/date fields from a combined line. Every non-null reference "
        "value must be exactly a listed cN alias; omit absent contact/link keys, "
        "use null only for nullable scalar fields, and use [] for empty arrays. "
        "For every wrapped professional-summary line, use the first line as summary "
        "and every continuation in source order as summary_lines. Put literal section "
        "heading aliases in section_headings (summary, skills, work_history, education, "
        "certifications, projects, licenses, languages, or a named:<index> key "
        "corresponding to named_sections[index]), never in the body arrays. "
        "Never return candidate text in source_references. CandidateProfile fields "
        "must contain normalized, human-readable values; cN aliases are forbidden "
        "outside source_references. Do not add `header` to CandidateProfile "
        "work-history objects. source_references is a semantic subset only: do not "
        "invent aliases and do not classify candidates merely for accounting; the "
        "server preserves valid unclassified source content."
    )
    prompt = base_prompt
    for attempt in range(_MAX_SOURCE_CONTRACT_ATTEMPTS):
        if qa_payload is not None:
            payload = dict(qa_payload)
            model = "qa-deterministic-tfr9"
        else:
            response, model = await llm_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                json_mode=True,
            )
            raw = response.choices[0].message.content or ""
            cleaned = strip_code_fences(raw)
            try:
                payload = json.loads(cleaned)
            except json.JSONDecodeError as exc:
                logger.warning("resume_parse_invalid_json", model=model, error=str(exc))
                raise ValueError("LLM did not return valid JSON for resume data") from exc
        if not isinstance(payload, dict):
            raise ValueError("LLM did not return a JSON object for resume data")

        raw_layout = payload.pop("source_references", None)
        # A model-generated source_content field is never an accepted input to
        # the canonical source pipeline. It is deliberately discarded.
        payload.pop("source_content", None)
        candidate_evidence = payload.pop("extraction_evidence", None)
        normalized_payload = _normalize_profile_payload(payload)
        alias_leak_paths = _profile_alias_leak_paths(normalized_payload, issued_aliases)
        if alias_leak_paths:
            logger.warning(
                "resume_profile_alias_leak",
                model=model,
                attempt=attempt + 1,
                error_paths=alias_leak_paths,
                error_count=len(alias_leak_paths),
            )
            if attempt + 1 >= _MAX_SOURCE_CONTRACT_ATTEMPTS:
                raise ValueError(
                    "Resume profile contained source aliases instead of normalized text"
                )
            prompt = _source_contract_retry_prompt(base_prompt)
            continue
        try:
            profile = ParsedProfile.model_validate(normalized_payload)
            evidence = _verify_extraction_evidence(
                candidate_evidence, resume_text, profile
            )
        except ValidationError as exc:
            logger.warning(
                "resume_parse_validation_failed",
                model=model,
                **_validation_log_fields(exc),
            )
            raise ValueError(f"Resume data failed validation: {exc}") from exc

        artifacts = classify_source_artifacts(ledger)
        layout_result = resolve_source_layout_v4(raw_layout, ledger, artifacts)
        if layout_result.status == "verified" and layout_result.layout is not None:
            header_errors = _required_header_layout_errors(profile, layout_result.layout, ledger)
            if header_errors:
                layout_result = SourceLayoutResult(None, "invalid", header_errors)
        if (
            layout_result.status == "verified"
            and not has_usable_tailoring_semantics(layout_result.layout)
        ):
            logger.warning(
                "resume_source_layout_zero_semantic_annotation",
                model=model,
                attempt=attempt + 1,
                error_codes=("zero_usable_semantic_annotation",),
                error_count=1,
            )
            if attempt + 1 >= _MAX_SOURCE_CONTRACT_ATTEMPTS:
                raise ValueError(
                    "Resume source layout has zero usable semantic annotation"
                )
            prompt = _source_contract_retry_prompt(base_prompt, zero_semantic=True)
            continue
        source_content = SourceResumeContentPayloadV4(
            document={
                "candidates": ledger.candidates,
                "layout": layout_result.layout,
                "artifacts": artifacts,
            },
            layout_validation_status=layout_result.status,
            layout_validation_errors=list(layout_result.errors),
        )
        if layout_result.status == "verified":
            return ParsedResumeResult(
                profile=profile,
                extraction_evidence=evidence,
                source_content=source_content,
                source_validation_status="verified",
                source_validation_errors=(),
                source_layout_status=layout_result.status,
                source_layout_errors=layout_result.errors,
            )
        error_codes = layout_result.errors or ("missing_source_references",)
        accounting_diagnostics: dict[str, object] = {}
        if "incomplete_source_accounting" in error_codes:
            missing_orders = layout_result.missing_candidate_orders
            accounting_diagnostics = {
                "missing_candidate_count": len(missing_orders),
                "missing_candidate_orders": missing_orders,
                "missing_candidate_aliases": tuple(f"c{order}" for order in missing_orders),
            }
        invalid_reference_diagnostics: dict[str, object] = {}
        if "invalid_source_references" in error_codes:
            diagnostics = layout_result.invalid_reference_diagnostics
            invalid_reference_diagnostics = {
                "invalid_reference_count": len(diagnostics),
                "invalid_reference_reasons": tuple(
                    diagnostic.reason for diagnostic in diagnostics
                ),
                "invalid_reference_paths": tuple(
                    diagnostic.path for diagnostic in diagnostics
                ),
                "invalid_reference_aliases": tuple(
                    diagnostic.alias for diagnostic in diagnostics
                ),
            }
        logger.warning(
            "resume_source_layout_validation_failed",
            model=model,
            attempt=attempt + 1,
            error_codes=error_codes,
            error_count=len(error_codes),
            **accounting_diagnostics,
            **invalid_reference_diagnostics,
        )
        if attempt + 1 >= _MAX_SOURCE_CONTRACT_ATTEMPTS:
            raise ValueError(
                "Resume source layout could not account for all source candidates: "
                + ", ".join(error_codes)
            )
        prompt = _source_contract_retry_prompt(
            base_prompt,
            error_codes=error_codes,
            invalid_reasons=tuple(
                diagnostic.reason
                for diagnostic in layout_result.invalid_reference_diagnostics
            ),
            invalid_paths=tuple(
                diagnostic.path
                for diagnostic in layout_result.invalid_reference_diagnostics
            ),
            invalid_aliases=tuple(
                diagnostic.alias
                for diagnostic in layout_result.invalid_reference_diagnostics
            ),
        )
    raise AssertionError("unreachable")


def _qa_fixture_payload(
    resume_text: str, ledger: SourceResumeContentDocumentV2
) -> dict[str, object] | None:
    """Return only the approved synthetic fixture's semantic response.

    This is deliberately fail-closed and never acts as an LLM/provider
    fallback. The normal profile, alias, ledger, and v3 verification pipeline
    still processes the returned payload below.
    """
    settings = get_settings()
    if not settings.qa_deterministic_parser or settings.environment not in {"development", "test"}:
        return None
    database_name = settings.database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if database_name != "job_app_test":
        return None
    if hashlib.sha256(resume_text.encode("utf-8")).hexdigest() != settings.qa_fixture_sha256:
        return None
    aliases = {f"c{candidate.order}": candidate.text for candidate in ledger.candidates}
    expected = {"c0", "c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9", "c10", "c11", "c12", "c13", "c14", "c15", "c16", "c17", "c18", "c19", "c20", "c21", "c22", "c23", "c24", "c25", "c26", "c27", "c28", "c29", "c30", "c31", "c32", "c33", "c34", "c35", "c36", "c37", "c38", "c39", "c40", "c41", "c42", "c43", "c44", "c45", "c46", "c47"}
    if set(aliases) != expected:
        return None
    return {
        "full_name": aliases["c0"],
        "headline": aliases["c1"],
        "location": aliases["c2"].split("|")[0].strip(),
        "email": aliases["c3"],
        "links": {"github": "https://" + aliases["c4"].split("|")[0].strip(), "portfolio": "https://" + aliases["c4"].split("|")[1].strip()},
        "professional_summary": aliases["c6"],
        "skills": [aliases[f"c{i}"] for i in range(8, 13)],
        "work_history": [
            {"company": "Aurora Robotics", "title": "Senior Software Engineer", "start": "2020-03", "end": None, "highlights": [aliases[f"c{i}"] for i in range(17, 22)]},
            {"company": "Brightline Data", "title": "Backend Engineer", "start": "2017-06", "end": "2020-02", "highlights": [aliases[f"c{i}"] for i in range(25, 29)]},
            {"company": "Sunrise Labs", "title": "Software Engineering Intern", "start": "2016-05", "end": "2016-08", "highlights": [aliases["c31"], aliases["c32"]]},
        ],
        "projects": [{"name": aliases["c35"], "description": aliases["c35"]}, {"name": aliases["c36"], "description": aliases["c36"]}],
        "education": [{"degree": aliases["c38"], "institution": "University of Washington", "year": "2012-09 to 2016-06"}],
        "certifications": [aliases["c40"]],
        "languages": [{"language": "English", "proficiency": "native"}, {"language": "Spanish", "proficiency": "conversational"}],
        "source_references": {"contact": {"name": "c0", "headline": "c1", "location": "c2", "email": "c3"}, "links": {"profiles": "c4"}, "summary": "c6", "skills": ["c8", "c9", "c10", "c11", "c12"], "section_headings": {"summary": "c5", "skills": "c7", "work_history": "c13", "education": "c37", "certifications": "c39", "languages": "c41", "named:0": "c33", "named:1": "c43", "named:2": "c46"}, "education": ["c38"], "certifications": ["c40"], "languages": ["c42"], "work_history": [{"order": 0, "header": "c14", "context": ["c15"], "responsibilities_heading": "c16", "highlights": [f"c{i}" for i in range(17, 22)]}, {"order": 1, "header": "c22", "context": ["c23"], "responsibilities_heading": "c24", "highlights": [f"c{i}" for i in range(25, 29)]}, {"order": 2, "header": "c29", "context": [], "responsibilities_heading": "c30", "highlights": ["c31", "c32"]}], "named_sections": [{"order": 33, "heading": "c33", "items": ["c34", "c35", "c36"]}, {"order": 43, "heading": "c43", "items": ["c44", "c45"]}, {"order": 46, "heading": "c46", "items": ["c47"]}]},
    }

    raise AssertionError("unreachable")


_EVIDENCE_CATEGORIES = {
    "work_history": "work_history",
    "education": "education",
    "certifications": "certifications",
    "projects": "projects",
    "languages": "languages",
    "licenses": "licenses",
}


def _normalize_evidence_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).casefold()
    return " ".join(normalized.split())


def _verify_extraction_evidence(
    candidate: object,
    source_text: str,
    profile: ParsedProfile,
) -> ExtractionEvidence:
    """Keep only bounded excerpts that match the extracted source text."""
    if not isinstance(candidate, dict):
        return ExtractionEvidence()

    normalized_source = _normalize_evidence_text(source_text)
    retained: dict[str, list[EvidenceItem]] = {}
    for category, profile_field in _EVIDENCE_CATEGORIES.items():
        raw_items = candidate.get(category)
        if not isinstance(raw_items, list):
            continue
        profile_items = getattr(profile, profile_field)
        seen_indices: set[int] = set()
        verified: list[EvidenceItem] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            item_index = raw_item.get("item_index")
            excerpt = raw_item.get("excerpt")
            if type(item_index) is not int or item_index < 0:
                continue
            if item_index in seen_indices or item_index >= len(profile_items):
                continue
            if not isinstance(excerpt, str):
                continue
            excerpt = excerpt.strip()
            if not 1 <= len(excerpt) <= 280:
                continue
            if _normalize_evidence_text(excerpt) not in normalized_source:
                continue
            verified.append(EvidenceItem(item_index=item_index, excerpt=excerpt))
            seen_indices.add(item_index)
        if verified:
            retained[category] = verified

    return ExtractionEvidence.model_validate(retained)
