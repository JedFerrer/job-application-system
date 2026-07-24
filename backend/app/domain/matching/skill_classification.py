"""Deterministic requirement classification for extracted job skill mentions."""

from __future__ import annotations

import re
from dataclasses import replace

from app.domain.matching.skill_types import SkillClassification, SkillExtractionReport

_REQUIRED_HEADING_RE = re.compile(
    r"^(?:requirements?|qualifications?|must\s+have|required(?:\s+skills?)?)"
    r"\s*[:\-]?\s*$",
    re.IGNORECASE,
)
_PREFERRED_HEADING_RE = re.compile(
    r"^(?:preferred(?:\s+skills?)?|nice\s+to\s+have|bonus|desired)"
    r"\s*[:\-]?\s*$",
    re.IGNORECASE,
)
_REQUIRED_MODAL_RE = re.compile(r"\b(required|must\s+have|minimum)\b", re.IGNORECASE)
_PREFERRED_MODAL_RE = re.compile(
    r"\b(preferred|bonus|a\s+plus|desired)\b", re.IGNORECASE
)
_BULLET_RE = re.compile(r"^(?:[-*+] |•|\d+[.)]\s+)")


def classify_skill_mentions(text: str, report: SkillExtractionReport) -> SkillExtractionReport:
    """Classify mentions by headings, bullets, explicit modals, and resets."""
    line_classifications = _line_classifications(text)
    classified = []
    for mention in report.mentions:
        classification = _classification_at_offset(mention.start_offset, line_classifications)
        classified.append(replace(mention, classification=classification))
    return SkillExtractionReport(
        mentions=tuple(classified), analyzer_version=report.analyzer_version
    ).deduplicated()


def _line_classifications(text: str) -> list[tuple[int, int, SkillClassification]]:
    lines: list[tuple[int, int, SkillClassification]] = []
    active_section: SkillClassification | None = None
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        line_end = offset + len(raw_line.rstrip("\r\n"))
        line = raw_line.strip()
        if not line:
            active_section = None
            offset += len(raw_line)
            continue

        heading = _heading_classification(line)
        modal = _modal_classification(line)
        is_bullet = bool(_BULLET_RE.match(line))
        if heading is not None:
            active_section = heading
            classification = heading
        elif is_bullet and active_section is not None:
            classification = active_section
        else:
            classification = modal or "unclassified"
            active_section = None
        lines.append((offset, line_end, classification))
        offset += len(raw_line)
    return lines


def _heading_classification(line: str) -> SkillClassification | None:
    if _PREFERRED_HEADING_RE.search(line):
        return "preferred"
    if _REQUIRED_HEADING_RE.search(line):
        return "required"
    return None


def _modal_classification(line: str) -> SkillClassification | None:
    if _REQUIRED_MODAL_RE.search(line):
        return "required"
    if _PREFERRED_MODAL_RE.search(line):
        return "preferred"
    return None


def _classification_at_offset(
    offset: int,
    lines: list[tuple[int, int, SkillClassification]],
) -> SkillClassification:
    for start, end, classification in lines:
        if start <= offset <= end:
            return classification
    return "unclassified"
