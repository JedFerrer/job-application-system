"""Canonical source-resume extraction and raw-text verification."""

from __future__ import annotations

import copy
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from app.domain.resume.schemas import (
    SOURCE_RESUME_SCHEMA_VERSION,
    SourceProject,
    SourceResumeCandidate,
    SourceResumeContentDocumentV2,
    SourceResumeContentPayload,
    SourceResumeDocument,
    SourceResumeLayoutReferences,
    SourceRole,
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


@dataclass(frozen=True)
class SourceLayoutResult:
    """Validated semantic layout resolved to server-owned candidate UUIDs."""

    layout: SourceResumeLayoutReferences | None
    status: Literal["verified", "invalid", "unavailable"]
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


def _raw_line_spans(raw_text: str) -> list[tuple[int, int]]:
    """Return non-empty physical line spans without crossing LF boundaries."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(raw_text):
        line_start = cursor
        newline = len(raw_text)
        for marker in ("\r", "\n"):
            found = raw_text.find(marker, cursor)
            if found >= 0:
                newline = min(newline, found)
        line_end = newline
        if raw_text[line_start:line_end].strip(" \t"):
            spans.append((line_start, line_end))
        if newline >= len(raw_text):
            break
        cursor = newline + 1
        if raw_text[newline] == "\r" and cursor < len(raw_text) and raw_text[cursor] == "\n":
            cursor += 1
    return spans


def _candidate_span(raw_text: str, start: int, end: int) -> tuple[int, int, str] | None:
    """Trim safe horizontal whitespace and a leading list marker only."""
    line = raw_text[start:end]
    left = len(line) - len(line.lstrip(" \t"))
    right = len(line.rstrip(" \t"))
    content_start = start + left
    content_end = start + right
    marker = re.match(r"(?:[•*]|[-–—]|\d+[.)])[ \t]+", raw_text[content_start:content_end])
    if marker is not None:
        content_start += marker.end()
    text = normalize_source_text(raw_text[content_start:content_end])
    if not text:
        return None
    return content_start, content_end, text


def build_source_candidate_ledger(raw_text: str) -> SourceResumeContentDocumentV2:
    """Create server-owned candidates from raw physical lines deterministically."""
    candidates: list[SourceResumeCandidate] = []
    occurrences: dict[str, int] = {}
    for line_start, line_end in _raw_line_spans(raw_text):
        candidate = _candidate_span(raw_text, line_start, line_end)
        if candidate is None:
            continue
        start_offset, end_offset, text = candidate
        ordinal = occurrences.get(text, 0)
        occurrences[text] = ordinal + 1
        candidates.append(
            SourceResumeCandidate(
                source_id=uuid4(),
                text=text,
                order=len(candidates),
                fingerprint=source_fingerprint(text),
                start_offset=start_offset,
                end_offset=end_offset,
                occurrence_ordinal=ordinal,
            )
        )
    return SourceResumeContentDocumentV2(candidates=candidates)


def verify_source_candidate_ledger(
    ledger: SourceResumeContentDocumentV2, raw_text: str
) -> tuple[str, ...]:
    """Verify server-created candidates before any persistence."""
    errors: list[str] = []
    previous_end = 0
    seen_orders: set[int] = set()
    seen_ids: set[UUID] = set()
    expected_occurrences: dict[str, int] = {}
    ordered_candidates = sorted(ledger.candidates, key=lambda item: item.order)
    if [candidate.order for candidate in ordered_candidates] != list(
        range(len(ordered_candidates))
    ):
        errors.append("candidate_order_not_contiguous")
    for candidate in ordered_candidates:
        if candidate.source_id in seen_ids or candidate.order in seen_orders:
            errors.append("duplicate_candidate_identity")
            continue
        seen_ids.add(candidate.source_id)
        seen_orders.add(candidate.order)
        if candidate.start_offset < previous_end or candidate.end_offset <= candidate.start_offset:
            errors.append("candidate_overlap_or_order")
            continue
        raw_slice = raw_text[candidate.start_offset : candidate.end_offset]
        if normalize_source_text(raw_slice) != normalize_source_text(candidate.text):
            errors.append("candidate_text_mismatch")
            continue
        if candidate.fingerprint != source_fingerprint(candidate.text):
            errors.append("candidate_fingerprint_mismatch")
            continue
        expected_ordinal = expected_occurrences.get(candidate.text, 0)
        if candidate.occurrence_ordinal != expected_ordinal:
            errors.append("candidate_occurrence_mismatch")
            continue
        expected_occurrences[candidate.text] = expected_ordinal + 1
        previous_end = candidate.end_offset
    return tuple(dict.fromkeys(errors))


def _resolve_candidate_alias(value: object, aliases: dict[str, UUID]) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in aliases:
        raise ValueError("unknown_candidate_alias")
    return aliases[value]


def _resolve_alias_list(value: object, aliases: dict[str, UUID]) -> list[UUID]:
    if not isinstance(value, list):
        raise ValueError("reference_list_expected")
    resolved: list[UUID] = []
    for item in value:
        candidate_id = _resolve_candidate_alias(item, aliases)
        if candidate_id is None:
            raise ValueError("null_candidate_reference")
        resolved.append(candidate_id)
    return resolved


def resolve_source_layout(
    raw_layout: object,
    ledger: SourceResumeContentDocumentV2,
) -> SourceLayoutResult:
    """Resolve model aliases to server UUIDs and reject unsafe layouts."""
    if raw_layout is None:
        return SourceLayoutResult(None, "unavailable", ("missing_source_references",))
    if not isinstance(raw_layout, dict):
        return SourceLayoutResult(None, "invalid", ("layout_object_expected",))
    aliases = {f"c{candidate.order}": candidate.source_id for candidate in ledger.candidates}
    candidate_orders = {candidate.source_id: candidate.order for candidate in ledger.candidates}

    def require_increasing(values: list[UUID]) -> None:
        orders = [candidate_orders[value] for value in values]
        if orders != sorted(orders):
            raise ValueError("reference_order_invalid")

    def resolve_map(value: object) -> dict[str, UUID]:
        if not isinstance(value, dict):
            raise ValueError("layout_map_expected")
        resolved: dict[str, UUID] = {}
        for key, raw_value in value.items():
            candidate_id = _resolve_candidate_alias(raw_value, aliases)
            if candidate_id is not None:
                resolved[key] = candidate_id
        return resolved

    try:
        allowed = {
            "contact", "links", "summary", "skills", "sections", "work_history",
            "projects", "education", "certifications", "licenses", "languages",
        }
        if set(raw_layout) - allowed:
            raise ValueError("unknown_layout_field")
        resolved: dict[str, object] = {
            "contact": resolve_map(raw_layout.get("contact", {})),
            "links": resolve_map(raw_layout.get("links", {})),
            "summary": _resolve_candidate_alias(raw_layout.get("summary"), aliases),
            "skills": _resolve_alias_list(raw_layout.get("skills", []), aliases),
            "sections": _resolve_alias_list(raw_layout.get("sections", []), aliases),
            "education": _resolve_alias_list(raw_layout.get("education", []), aliases),
            "certifications": _resolve_alias_list(raw_layout.get("certifications", []), aliases),
            "licenses": _resolve_alias_list(raw_layout.get("licenses", []), aliases),
            "languages": _resolve_alias_list(raw_layout.get("languages", []), aliases),
        }
        roles = raw_layout.get("work_history", [])
        projects = raw_layout.get("projects", [])
        if not isinstance(roles, list) or not isinstance(projects, list):
            raise ValueError("layout_collection_expected")
        resolved_roles: list[dict[str, object]] = []
        for _index, role in enumerate(roles):
            if not isinstance(role, dict):
                raise ValueError("role_object_expected")
            if set(role) - {"order", "header", "highlights"}:
                raise ValueError("unknown_role_field")
            header = _resolve_candidate_alias(role.get("header"), aliases)
            if header is None:
                raise ValueError("role_header_required")
            resolved_roles.append({
                # The model's numeric order is deliberately ignored. Canonical
                # role order is owned by the server-side header candidate.
                "order": candidate_orders[header],
                "header": header,
                "highlights": _resolve_alias_list(role.get("highlights", []), aliases),
            })
        resolved_roles.sort(key=lambda role: cast(int, role["order"]))
        resolved["work_history"] = resolved_roles
        resolved_projects: list[dict[str, object]] = []
        for index, project in enumerate(projects):
            if not isinstance(project, dict):
                raise ValueError("project_object_expected")
            if set(project) - {
                "order",
                "name",
                "description",
                "url",
                "technologies",
                "achievements",
            }:
                raise ValueError("unknown_project_field")
            resolved_projects.append({
                **{
                    key: _resolve_candidate_alias(project.get(key), aliases)
                    for key in ("name", "description", "url")
                },
                "order": project.get("order", index),
                "technologies": _resolve_alias_list(project.get("technologies", []), aliases),
                "achievements": _resolve_alias_list(project.get("achievements", []), aliases),
            })
        resolved["projects"] = resolved_projects
        layout = SourceResumeLayoutReferences.model_validate(resolved)
        require_increasing(layout.skills)
        require_increasing(layout.sections)
        require_increasing(layout.education)
        require_increasing(layout.certifications)
        require_increasing(layout.licenses)
        require_increasing(layout.languages)
        for role in layout.work_history:
            require_increasing(role.highlights)
        for project in layout.projects:
            require_increasing(project.technologies)
            require_increasing(project.achievements)
        # Contact and link maps are semantic metadata: one physical line can
        # legitimately provide multiple fields (for example, email and phone)
        # and therefore may be referenced more than once. All prose/content
        # references retain exclusive candidate ownership. A metadata
        # candidate may not also be claimed by exclusive content, which keeps
        # the relaxation narrow and prevents cross-category ambiguity.
        metadata_referenced: list[UUID] = []
        metadata_referenced.extend(layout.contact.values())
        metadata_referenced.extend(layout.links.values())
        exclusive_referenced: list[UUID] = []
        exclusive_referenced.extend(item for item in [layout.summary] if item is not None)
        exclusive_referenced.extend(layout.skills + layout.sections + layout.education)
        exclusive_referenced.extend(layout.certifications + layout.licenses + layout.languages)
        for role in layout.work_history:
            exclusive_referenced.extend(item for item in [role.header] if item is not None)
            exclusive_referenced.extend(role.highlights)
        for project in layout.projects:
            exclusive_referenced.extend(
                item for item in (project.name, project.description, project.url)
                if item is not None
            )
            exclusive_referenced.extend(project.technologies + project.achievements)
        if not metadata_referenced and not exclusive_referenced:
            raise ValueError("empty_source_references")
        if len(exclusive_referenced) != len(set(exclusive_referenced)):
            raise ValueError("duplicate_candidate_reference")
        if set(metadata_referenced) & set(exclusive_referenced):
            raise ValueError("duplicate_candidate_reference")
        return SourceLayoutResult(layout, "verified")
    except (TypeError, ValueError):
        return SourceLayoutResult(None, "invalid", ("invalid_source_references",))


def materialize_source_document(
    ledger: SourceResumeContentDocumentV2,
) -> SourceResumeDocument | None:
    """Materialize legacy tailoring fields from verified v2 references only."""
    if ledger.layout is None:
        return None
    by_id = {
        candidate.source_id: SourceTextItem.model_validate(candidate.model_dump())
        for candidate in ledger.candidates
    }

    def item(candidate_id: UUID | None) -> SourceTextItem | None:
        return by_id.get(candidate_id) if candidate_id is not None else None

    layout = ledger.layout
    return SourceResumeDocument(
        contact={key: by_id[value] for key, value in layout.contact.items()},
        links={key: by_id[value] for key, value in layout.links.items()},
        summary=item(layout.summary),
        skills=[by_id[value] for value in layout.skills],
        sections=[by_id[value] for value in layout.sections],
        work_history=[
            SourceRole(
                order=role.order,
                # A combined raw header is the sole canonical identity source;
                # it is intentionally reused for legacy company/title slots
                # without inventing substrings or accepting model prose.
                company=item(role.header),
                title=item(role.header),
                highlights=[by_id[value] for value in role.highlights],
            )
            for role in layout.work_history
        ],
        projects=[
            SourceProject(
                order=project.order,
                name=item(project.name),
                description=item(project.description),
                url=item(project.url),
                technologies=[by_id[value] for value in project.technologies],
                achievements=[by_id[value] for value in project.achievements],
            )
            for project in layout.projects
        ],
        education=[by_id[value] for value in layout.education],
        certifications=[by_id[value] for value in layout.certifications],
        licenses=[by_id[value] for value in layout.licenses],
        languages=[by_id[value] for value in layout.languages],
    )


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
