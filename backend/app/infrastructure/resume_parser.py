"""Resume file parsing: byte extraction (PDF/DOCX) + LLM structuring.

Ported from hr-ai-nterviewer-saas (``services/resume_service.py``). File text
extraction lives here (infrastructure); the LLM call returns a validated
``ParsedProfile``.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from typing import Any, Literal, NamedTuple, overload

import pdfplumber
from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
from pydantic import ValidationError

from app.core.logging import get_logger
from app.domain.resume.prompts import RESUME_EXTRACTION_PROMPT
from app.domain.resume.schemas import ExtractionWarning, ParsedProfile
from app.infrastructure.llm_client import llm_chat_completion
from app.infrastructure.llm_utils import strip_code_fences

logger = get_logger("resume_parser")

SUPPORTED_EXTENSIONS = {"pdf", "docx"}
# Defence-in-depth size cap (handlers also enforce this at the boundary).
MAX_RESUME_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
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


async def parse_resume_text(resume_text: str) -> ParsedProfile:
    """Send resume text to the LLM and return a validated ``ParsedProfile``.

    Raises ``ValueError`` when the LLM output is not valid JSON or fails schema
    validation (normalized so callers handle a single exception type).
    """
    prompt = RESUME_EXTRACTION_PROMPT.format(resume_text=resume_text)
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

    # Log work history highlights for debugging.
    wh = payload.get("work_history") or payload.get("experience") or []
    for i, role in enumerate(wh if isinstance(wh, list) else []):
        if isinstance(role, dict):
            h = role.get("highlights", [])
            logger.debug(
                "resume_parse.role_highlights",
                role_index=i,
                title=role.get("title"),
                highlights_count=len(h) if isinstance(h, list) else "not_list",
                highlights_sample=str(h)[:200],
            )

    try:
        profile = ParsedProfile.model_validate(payload)
        # Log what actually got validated.
        for i, item in enumerate(profile.work_history):
            logger.debug(
                "resume_parse.validated_highlights",
                role_index=i,
                title=item.title,
                highlights_count=len(item.highlights),
            )
        return profile
    except ValidationError as exc:
        logger.warning("resume_parse_validation_failed", model=model, error=str(exc))
        raise ValueError(f"Resume data failed validation: {exc}") from exc
