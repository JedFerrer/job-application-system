"""Canonical source-resume extraction and raw-text verification."""

from __future__ import annotations

import copy
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from app.domain.resume.schemas import (
    SOURCE_RESUME_SCHEMA_VERSION,
    SourceResumeContentPayload,
    SourceResumeDocument,
    SourceTextItem,
)

SOURCE_VALIDATION_VERSION = "source-verifier-v1"
_LIST_MARKER_RE = re.compile(r"(?:[•*]|[-–—]|\d+[.)])[ \t]+$")


@dataclass(frozen=True)
class _MappedView:
    text: str
    starts: tuple[int, ...]
    ends: tuple[int, ...]


@dataclass(frozen=True)
class SourceVerificationResult:
    payload: SourceResumeContentPayload | None
    status: Literal["verified", "invalid"]
    errors: tuple[str, ...] = ()


def normalize_source_text(value: str) -> str:
    """Normalize approved Unicode, line-ending, and horizontal differences."""
    value = unicodedata.normalize("NFC", value).replace("\u00a0", " ")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[^\S\r\n]+", " ", value)
    return value.strip(" \t")


def _normalize_raw_slice(raw: str) -> str:
    return normalize_source_text(raw)


def source_fingerprint(value: str) -> str:
    return hashlib.sha256(normalize_source_text(value).encode("utf-8")).hexdigest()


def _mapped_view(raw: str, *, compact_soft_wrap: bool) -> _MappedView:
    """Build a normalized view while retaining coordinates in original raw text."""
    chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    index = 0
    pending_space: tuple[int, int] | None = None
    while index < len(raw):
        start = index
        if raw[index] in "\r\n":
            index += 1
            if raw[start] == "\r" and index < len(raw) and raw[index] == "\n":
                index += 1
            pending_space = None
            chars.append("\n")
            starts.append(start)
            ends.append(index)
            continue
        # Normalize one base character plus combining marks as one unit. This
        # preserves raw coordinates when NFC composes decomposed characters.
        index += 1
        while index < len(raw) and unicodedata.combining(raw[index]):
            index += 1
        unit = unicodedata.normalize("NFC", raw[start:index]).replace("\u00a0", " ")
        if unit.isspace():
            if pending_space is None:
                pending_space = (start, index)
            else:
                pending_space = (pending_space[0], index)
            continue
        if pending_space is not None:
            chars.append(" ")
            starts.append(pending_space[0])
            ends.append(pending_space[1])
            pending_space = None
        for char in unit:
            chars.append(char)
            starts.append(start)
            ends.append(index)
    left = 0
    right = len(chars)
    while left < right and chars[left].isspace():
        left += 1
    while right > left and chars[right - 1].isspace():
        right -= 1
    return _MappedView(
        "".join(chars[left:right]),
        tuple(starts[left:right]),
        tuple(ends[left:right]),
    )


def _candidate_views(value: str) -> tuple[str, ...]:
    return (normalize_source_text(value),)


def _is_complete_structural_span(raw: str, start: int, end: int) -> bool:
    """Require a whole line item, optionally following one list marker."""
    line_start = max(raw.rfind("\n", 0, start), raw.rfind("\r", 0, start)) + 1
    line_end_candidates = [
        index for index in (raw.find("\n", end), raw.find("\r", end)) if index >= 0
    ]
    line_end = min(line_end_candidates, default=len(raw))
    prefix = raw[line_start:start]
    suffix = raw[end:line_end]
    if suffix.strip(" \t"):
        return False
    if not prefix.strip(" \t"):
        return True
    return bool(_LIST_MARKER_RE.fullmatch(prefix))


def _find_occurrence(
    raw: str,
    candidate: str,
    *,
    minimum_raw_offset: int,
    used: set[tuple[int, int]],
    allow_ordered_duplicate: bool,
) -> tuple[int, int, int] | None:
    matches: list[tuple[int, int]] = []
    for compact in (False,):
        view = _mapped_view(raw, compact_soft_wrap=compact)
        for candidate_view in _candidate_views(candidate):
            start = 0
            while True:
                position = view.text.find(candidate_view, start)
                if position < 0:
                    break
                end_position = position + len(candidate_view)
                if end_position <= len(view.starts):
                    span = (view.starts[position], view.ends[end_position - 1])
                    left_boundary = span[0] == 0 or raw[span[0] - 1].isspace()
                    right_boundary = span[1] == len(raw) or raw[span[1]].isspace()
                    if (
                        span[0] >= minimum_raw_offset
                        and span not in used
                        and left_boundary
                        and right_boundary
                        and _is_complete_structural_span(raw, *span)
                    ):
                        matches.append(span)
                start = position + 1
    unique = sorted(set(matches))
    if not unique or (len(unique) > 1 and not allow_ordered_duplicate):
        return None
    same_start = {end for start, end in unique if start == unique[0][0]}
    if len(same_start) > 1:
        return None
    span = unique[0]
    normalized = normalize_source_text(candidate)
    all_spans: set[tuple[int, int]] = set()
    for compact in (False,):
        view = _mapped_view(raw, compact_soft_wrap=compact)
        for candidate_view in _candidate_views(candidate):
            search_from = 0
            while True:
                position = view.text.find(candidate_view, search_from)
                if position < 0:
                    break
                end_position = position + len(candidate_view)
                if end_position <= len(view.starts):
                    candidate_span = (
                        view.starts[position], view.ends[end_position - 1]
                    )
                    if (
                        _normalize_raw_slice(raw[candidate_span[0] : candidate_span[1]])
                        == normalized
                    ):
                        all_spans.add(candidate_span)
                search_from = position + 1
    all_spans.add(span)
    ordinal = sorted(all_spans).index(span)
    return span[0], span[1], ordinal


def _iter_text_items(document: SourceResumeDocument) -> list[SourceTextItem]:
    items: list[SourceTextItem] = []
    items.extend(document.contact.values())
    items.extend(document.links.values())
    if document.summary:
        items.append(document.summary)
    items.extend(document.skills)
    items.extend(document.sections)
    for role in document.work_history:
        for metadata in (role.company, role.title, role.start, role.end):
            if metadata:
                items.append(metadata)
        if role.description:
            items.append(role.description)
        items.extend(role.highlights)
    for project in document.projects:
        for item in (project.name, project.description, project.url):
            if item:
                items.append(item)
        items.extend(project.technologies)
        items.extend(project.achievements)
    for collection in (
        document.education,
        document.certifications,
        document.licenses,
        document.languages,
    ):
        items.extend(collection)
    return sorted(items, key=lambda item: item.order)


def verify_source_document(candidate: Any, raw_text: str) -> SourceVerificationResult:
    """Verify every candidate text item and return a safe canonical payload."""
    # Parser output is untrusted.  Never accept identity or verification
    # metadata supplied by the model; those fields are hydrated below only
    # after a raw-text span has been proven.
    if isinstance(candidate, dict):
        candidate = copy.deepcopy(candidate)

        def strip_metadata(value: Any) -> None:
            if isinstance(value, dict):
                for key in (
                    "source_id",
                    "fingerprint",
                    "start_offset",
                    "end_offset",
                    "occurrence_ordinal",
                ):
                    value.pop(key, None)
                for child in value.values():
                    strip_metadata(child)
            elif isinstance(value, list):
                for child in value:
                    strip_metadata(child)

        strip_metadata(candidate)
    try:
        document = SourceResumeDocument.model_validate(candidate)
    except Exception as exc:
        return SourceVerificationResult(None, "invalid", (f"malformed_document:{exc}",))
    if not raw_text.strip():
        return SourceVerificationResult(None, "invalid", ("empty_raw_text",))
    items = _iter_text_items(document)
    if not items:
        return SourceVerificationResult(None, "invalid", ("no_source_items",))
    orders = [item.order for item in items]
    if len(set(orders)) != len(orders):
        return SourceVerificationResult(None, "invalid", ("duplicate_source_order",))
    used: set[tuple[int, int]] = set()
    errors: list[str] = []
    previous_end = 0
    candidate_counts: dict[str, int] = {}
    for item in items:
        candidate_counts[normalize_source_text(item.text)] = (
            candidate_counts.get(normalize_source_text(item.text), 0) + 1
        )
    for item in items:
        if not isinstance(item.text, str) or not item.text.strip():
            errors.append("empty_text_item")
            continue
        occurrence = _find_occurrence(
            raw_text,
            item.text,
            minimum_raw_offset=previous_end,
            used=used,
            allow_ordered_duplicate=(candidate_counts[normalize_source_text(item.text)] > 1),
        )
        if occurrence is None:
            errors.append(f"unverifiable:{item.source_id}")
            continue
        start, end, ordinal = occurrence
        if start < previous_end or (start, end) in used:
            errors.append(f"overlap_or_order:{item.source_id}")
            continue
        item.start_offset = start
        item.end_offset = end
        item.text = _normalize_raw_slice(raw_text[start:end])
        item.fingerprint = source_fingerprint(item.text)
        item.occurrence_ordinal = ordinal
        used.add((start, end))
        previous_end = end
    if errors:
        # Do not return candidate text from an invalid result: callers cannot
        # accidentally persist or expose rejected text as source evidence.
        return SourceVerificationResult(None, "invalid", tuple(errors))
    return SourceVerificationResult(
        SourceResumeContentPayload(
            schema_version=SOURCE_RESUME_SCHEMA_VERSION, document=document
        ),
        "verified",
    )
