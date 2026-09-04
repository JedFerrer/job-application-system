# ruff: noqa: E501
"""Canonical source-resume extraction and raw-text verification."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.domain.resume.schemas import (
    SOURCE_RESUME_SCHEMA_VERSION,
    SourceNamedSection,
    SourceProject,
    SourceResumeArtifact,
    SourceResumeCandidate,
    SourceResumeContentDocumentV2,
    SourceResumeContentDocumentV3,
    SourceResumeContentDocumentV4,
    SourceResumeContentPayload,
    SourceResumeDocument,
    SourceResumeLayoutReferences,
    SourceResumeLayoutReferencesV3,
    SourceResumeLayoutReferencesV4,
    SourceResumeNamedSectionReferences,
    SourceResumeRoleReferencesV3,
    SourceRole,
    SourceTextItem,
)

SOURCE_VALIDATION_VERSION = "source-verifier-v1"


def source_revision_sha256(payload: object) -> str:
    """Return a stable revision for a verified canonical source payload."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
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
    # Source order is a safe, request-local diagnostic identifier. Keep source
    # text, UUIDs, offsets, and fingerprints out of validation results.
    missing_candidate_orders: tuple[int, ...] = ()
    invalid_reference_diagnostics: tuple[InvalidReferenceDiagnostic, ...] = ()


@dataclass(frozen=True)
class InvalidReferenceDiagnostic:
    """One privacy-safe structural source-reference validation failure."""

    reason: str
    path: str
    alias: str | None = None
    expected_section: str | None = None


class _InvalidSourceReferenceError(ValueError):
    """Internal fail-fast error carrying only controlled diagnostic data."""

    def __init__(self, reason: str, path: str, alias: str | None = None) -> None:
        super().__init__(reason)
        self.diagnostic = InvalidReferenceDiagnostic(reason, path, alias)


def _invalid_reference_result(
    *diagnostics: InvalidReferenceDiagnostic,
) -> SourceLayoutResult:
    return SourceLayoutResult(
        None,
        "invalid",
        ("invalid_source_references",),
        invalid_reference_diagnostics=diagnostics,
    )


_SAFE_REFERENCE_PATH_PARTS = {
    "contact", "links", "summary", "skills", "sections", "work_history",
    "projects", "education", "certifications", "licenses", "languages",
    "named_sections", "order", "header", "highlights", "context",
    "responsibilities_heading", "name", "description", "url", "technologies",
    "achievements", "heading", "items",
}


def _schema_validation_diagnostics(exc: ValidationError) -> tuple[InvalidReferenceDiagnostic, ...]:
    """Extract only fixed schema paths; never retain Pydantic's rejected input."""
    diagnostics: list[InvalidReferenceDiagnostic] = []
    for error in exc.errors():
        path = "source_references"
        parts: list[str] = []
        for part in error.get("loc", ()):
            if isinstance(part, int):
                if parts:
                    parts[-1] += f"[{part}]"
                continue
            if isinstance(part, str) and part in _SAFE_REFERENCE_PATH_PARTS:
                parts.append(part)
                continue
            parts = []
            break
        if parts:
            path = ".".join(parts)
        diagnostics.append(InvalidReferenceDiagnostic("schema_validation_failed", path))
    return tuple(diagnostics) or (InvalidReferenceDiagnostic("schema_validation_failed", "source_references"),)


_SEPARATOR_RE = re.compile(r"^[\s\W_]{3,}$")


def classify_source_artifacts(
    ledger: SourceResumeContentDocumentV2,
) -> list[SourceResumeArtifact]:
    """Classify only unambiguous mechanical separators as non-display artifacts."""
    return [
        SourceResumeArtifact(source_id=candidate.source_id, reason="mechanical_separator")
        for candidate in ledger.candidates
        if _SEPARATOR_RE.fullmatch(candidate.text)
        and not any(character.isalnum() for character in candidate.text)
    ]


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
    text = re.sub(r"\s*\n\s*", " ", normalize_source_text(raw_text[content_start:content_end]))
    if not text:
        return None
    return content_start, content_end, text


_SOURCE_HEADINGS = {
    "professional summary", "summary", "technical stack", "technical skills",
    "professional experience", "work experience", "experience", "education",
    "certifications", "awards and achievements", "awards & achievements",
    "projects", "languages", "licenses", "innovation & side projects",
    "innovation and side projects",
}


_ROLE_HEADER_SEPARATOR_RE = re.compile(r"\s[-–—|]\s")
_ROLE_HEADER_DATE_RANGE_RE = re.compile(
    r"\b(?:19|20)\d{2}\s*[-–—]\s*(?:(?:19|20)\d{2}|present|current)\b",
    re.IGNORECASE,
)


def _is_list_marker(raw_text: str, start: int, end: int) -> bool:
    line = raw_text[start:end].lstrip(" \t")
    return re.match(r"(?:[•*]|[-–—]|\d+[.)])[ \t]+", line) is not None


def _is_responsibilities_heading_text(text: str) -> bool:
    return "responsibilit" in text.casefold()


def _looks_like_role_header(text: str) -> bool:
    """Reject only structurally header-like lines; never match resume wording."""
    stripped = normalize_source_text(text)
    if not stripped:
        return False
    first = next((character for character in stripped if character.isalnum()), "")
    if not first.isalpha() or not first.isupper():
        return False
    return bool(
        _ROLE_HEADER_SEPARATOR_RE.search(stripped)
        or _ROLE_HEADER_DATE_RANGE_RE.search(stripped)
    )


def _starts_with_lowercase_alpha(text: str) -> bool:
    stripped = normalize_source_text(text)
    first = next((character for character in stripped if not character.isspace()), "")
    return first.isalpha() and first.islower()


def _previous_looks_like_wrap(previous_text: str) -> bool:
    previous = previous_text.casefold().rstrip()
    return previous.endswith((",", ";", ":", "-")) or previous.endswith(
        (" and", " or", " with", " to")
    )


def _is_compatible_bullet_continuation(
    raw_text: str,
    previous_start: int,
    previous_end: int,
    next_start: int,
    next_end: int,
) -> bool:
    """Attach a wrap only to the immediately preceding structurally compatible bullet."""
    next_text = normalize_source_text(raw_text[next_start:next_end])
    folded = next_text.casefold().strip()
    if (
        folded in _SOURCE_HEADINGS
        or _is_list_marker(raw_text, next_start, next_end)
        or _is_responsibilities_heading_text(next_text)
        or _looks_like_role_header(next_text)
    ):
        return False
    indented = raw_text[next_start:next_end] != raw_text[next_start:next_end].lstrip(" \t")
    previous_text = normalize_source_text(raw_text[previous_start:previous_end])
    return bool(
        indented
        or _previous_looks_like_wrap(previous_text)
        or _starts_with_lowercase_alpha(next_text)
    )


def _logical_source_spans(raw_text: str) -> list[tuple[int, int]]:
    """Group visual wraps into logical source statements without crossing headings."""
    physical = _raw_line_spans(raw_text)
    grouped: list[tuple[int, int]] = []
    index = 0
    while index < len(physical):
        start, end = physical[index]
        text = normalize_source_text(raw_text[start:end]).casefold().strip()
        is_heading = text in _SOURCE_HEADINGS
        is_bullet = _is_list_marker(raw_text, start, end)
        if is_heading:
            grouped.append((start, end))
            index += 1
            continue
        if is_bullet:
            last_end = end
            index += 1
            while index < len(physical):
                next_start, next_end = physical[index]
                if not _is_compatible_bullet_continuation(
                    raw_text, start, last_end, next_start, next_end
                ):
                    break
                last_end = next_end
                index += 1
            grouped.append((start, last_end))
            continue
        grouped.append((start, end))
        index += 1
    return grouped


def build_source_candidate_ledger(raw_text: str) -> SourceResumeContentDocumentV2:
    """Create server-owned candidates from logical source statements deterministically."""
    candidates: list[SourceResumeCandidate] = []
    occurrences: dict[str, int] = {}
    for line_start, line_end in _logical_source_spans(raw_text):
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
                kind=(
                    "heading"
                    if text.casefold() in _SOURCE_HEADINGS
                    else (
                        "bullet"
                        if _is_list_marker(
                            raw_text,
                            max(raw_text.rfind("\n", 0, start_offset), raw_text.rfind("\r", 0, start_offset)) + 1,
                            start_offset,
                        )
                        else "text"
                    )
                ),
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
        raw_normalized = normalize_source_text(raw_slice)
        candidate_normalized = normalize_source_text(candidate.text)
        if "\n" in raw_slice or "\r" in raw_slice:
            raw_normalized = re.sub(r"\s*\n\s*", " ", raw_normalized)
            candidate_normalized = re.sub(r"\s*\n\s*", " ", candidate_normalized)
        if raw_normalized != candidate_normalized:
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


def _resolve_candidate_alias(
    value: object,
    aliases: dict[str, UUID],
    *,
    path: str,
) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in aliases:
        alias = value if isinstance(value, str) and re.fullmatch(r"c\d+", value) else None
        raise _InvalidSourceReferenceError("unknown_candidate_alias", path, alias)
    return aliases[value]


def _resolve_alias_list(
    value: object,
    aliases: dict[str, UUID],
    *,
    path: str,
) -> list[UUID]:
    if not isinstance(value, list):
        raise _InvalidSourceReferenceError("reference_list_expected", path)
    resolved: list[UUID] = []
    for index, item in enumerate(value):
        candidate_id = _resolve_candidate_alias(item, aliases, path=f"{path}[{index}]")
        if candidate_id is None:
            raise _InvalidSourceReferenceError(
                "null_candidate_reference", f"{path}[{index}]"
            )
        resolved.append(candidate_id)
    return resolved


def resolve_source_layout(
    raw_layout: object,
    ledger: SourceResumeContentDocumentV2,
    *,
    normalize_order: bool = False,
    allow_empty: bool = False,
) -> SourceLayoutResult:
    """Resolve model aliases to server UUIDs and reject unsafe layouts."""
    if raw_layout is None:
        return SourceLayoutResult(None, "unavailable", ("missing_source_references",))
    if not isinstance(raw_layout, dict):
        return SourceLayoutResult(None, "invalid", ("layout_object_expected",))
    aliases = {f"c{candidate.order}": candidate.source_id for candidate in ledger.candidates}
    candidate_orders = {candidate.source_id: candidate.order for candidate in ledger.candidates}

    def require_increasing(values: list[UUID], path: str) -> None:
        orders = [candidate_orders[value] for value in values]
        if orders != sorted(orders):
            for index in range(1, len(orders)):
                if orders[index] < orders[index - 1]:
                    raise _InvalidSourceReferenceError(
                        "reference_order_invalid",
                        f"{path}[{index}]",
                        f"c{orders[index]}",
                    )
            raise _InvalidSourceReferenceError("reference_order_invalid", path)

    def resolve_map(value: object, path: str) -> dict[str, UUID]:
        if not isinstance(value, dict):
            raise _InvalidSourceReferenceError("layout_map_expected", path)
        resolved: dict[str, UUID] = {}
        for key, raw_value in value.items():
            # Map keys are model-provided and could contain personal data. Keep
            # diagnostics at the stable map level rather than exposing a key.
            candidate_id = _resolve_candidate_alias(raw_value, aliases, path=path)
            if candidate_id is not None:
                resolved[key] = candidate_id
        return resolved

    try:
        allowed = {
            "contact", "links", "summary", "skills", "sections", "work_history",
            "projects", "education", "certifications", "licenses", "languages",
        }
        if set(raw_layout) - allowed:
            raise _InvalidSourceReferenceError("unknown_layout_field", "source_references")
        resolved: dict[str, object] = {
            "contact": resolve_map(raw_layout.get("contact", {}), "contact"),
            "links": resolve_map(raw_layout.get("links", {}), "links"),
            "summary": _resolve_candidate_alias(
                raw_layout.get("summary"), aliases, path="summary"
            ),
            "skills": _resolve_alias_list(raw_layout.get("skills", []), aliases, path="skills"),
            "sections": _resolve_alias_list(raw_layout.get("sections", []), aliases, path="sections"),
            "education": _resolve_alias_list(raw_layout.get("education", []), aliases, path="education"),
            "certifications": _resolve_alias_list(raw_layout.get("certifications", []), aliases, path="certifications"),
            "licenses": _resolve_alias_list(raw_layout.get("licenses", []), aliases, path="licenses"),
            "languages": _resolve_alias_list(raw_layout.get("languages", []), aliases, path="languages"),
        }
        roles = raw_layout.get("work_history", [])
        projects = raw_layout.get("projects", [])
        if not isinstance(roles, list) or not isinstance(projects, list):
            path = "work_history" if not isinstance(roles, list) else "projects"
            raise _InvalidSourceReferenceError("layout_collection_expected", path)
        resolved_roles: list[dict[str, object]] = []
        for index, role in enumerate(roles):
            if not isinstance(role, dict):
                raise _InvalidSourceReferenceError("role_object_expected", f"work_history[{index}]")
            if set(role) - {"order", "header", "highlights"}:
                raise _InvalidSourceReferenceError("unknown_role_field", f"work_history[{index}]")
            header = _resolve_candidate_alias(
                role.get("header"), aliases, path=f"work_history[{index}].header"
            )
            if header is None:
                raise _InvalidSourceReferenceError(
                    "role_header_required", f"work_history[{index}].header"
                )
            resolved_roles.append({
                # The model's numeric order is deliberately ignored. Canonical
                # role order is owned by the server-side header candidate.
                "order": candidate_orders[header],
                "header": header,
                "highlights": _resolve_alias_list(
                    role.get("highlights", []), aliases, path=f"work_history[{index}].highlights"
                ),
            })
        resolved_roles.sort(key=lambda role: cast(int, role["order"]))
        resolved["work_history"] = resolved_roles
        resolved_projects: list[dict[str, object]] = []
        for index, project in enumerate(projects):
            if not isinstance(project, dict):
                raise _InvalidSourceReferenceError("project_object_expected", f"projects[{index}]")
            if set(project) - {
                "order",
                "name",
                "description",
                "url",
                "technologies",
                "achievements",
            }:
                raise _InvalidSourceReferenceError("unknown_project_field", f"projects[{index}]")
            resolved_projects.append({
                **{
                    key: _resolve_candidate_alias(
                        project.get(key), aliases, path=f"projects[{index}].{key}"
                    )
                    for key in ("name", "description", "url")
                },
                "order": project.get("order", index),
                "technologies": _resolve_alias_list(
                    project.get("technologies", []), aliases,
                    path=f"projects[{index}].technologies",
                ),
                "achievements": _resolve_alias_list(
                    project.get("achievements", []), aliases,
                    path=f"projects[{index}].achievements",
                ),
            })
        resolved["projects"] = resolved_projects
        layout = SourceResumeLayoutReferences.model_validate(resolved)
        if normalize_order:
            order_key = candidate_orders.__getitem__
            layout = layout.model_copy(
                update={
                    "skills": sorted(layout.skills, key=order_key),
                    "sections": sorted(layout.sections, key=order_key),
                    "education": sorted(layout.education, key=order_key),
                    "certifications": sorted(layout.certifications, key=order_key),
                    "licenses": sorted(layout.licenses, key=order_key),
                    "languages": sorted(layout.languages, key=order_key),
                    "work_history": [
                        role.model_copy(
                            update={
                                "highlights": sorted(role.highlights, key=order_key),
                            }
                        )
                        for role in layout.work_history
                    ],
                    "projects": [
                        project.model_copy(
                            update={
                                "technologies": sorted(project.technologies, key=order_key),
                                "achievements": sorted(project.achievements, key=order_key),
                            }
                        )
                        for project in layout.projects
                    ],
                }
            )
        else:
            require_increasing(layout.skills, "skills")
            require_increasing(layout.sections, "sections")
            require_increasing(layout.education, "education")
            require_increasing(layout.certifications, "certifications")
            require_increasing(layout.licenses, "licenses")
            require_increasing(layout.languages, "languages")
            for index, role in enumerate(layout.work_history):
                require_increasing(role.highlights, f"work_history[{index}].highlights")
            for index, project in enumerate(layout.projects):
                require_increasing(project.technologies, f"projects[{index}].technologies")
                require_increasing(project.achievements, f"projects[{index}].achievements")
        # Contact and link maps are semantic metadata: one physical line can
        # legitimately provide multiple fields (for example, email and phone)
        # and therefore may be referenced more than once. All prose/content
        # references retain exclusive candidate ownership. A metadata
        # candidate may not also be claimed by exclusive content, which keeps
        # the relaxation narrow and prevents cross-category ambiguity.
        metadata_owners: dict[UUID, list[str]] = {}
        exclusive_owners: dict[UUID, list[str]] = {}

        def own(mapping: dict[UUID, list[str]], candidate_id: UUID, path: str) -> None:
            mapping.setdefault(candidate_id, []).append(path)

        for field_name, candidate_id in layout.contact.items():
            own(metadata_owners, candidate_id, f"contact.{field_name}")
        for field_name, candidate_id in layout.links.items():
            own(metadata_owners, candidate_id, f"links.{field_name}")
        if layout.summary is not None:
            own(exclusive_owners, layout.summary, "summary")
        for field_name, values in (
            ("skills", layout.skills),
            ("sections", layout.sections),
            ("education", layout.education),
            ("certifications", layout.certifications),
            ("licenses", layout.licenses),
            ("languages", layout.languages),
        ):
            for index, candidate_id in enumerate(values):
                own(exclusive_owners, candidate_id, f"{field_name}[{index}]")
        for role_index, role in enumerate(layout.work_history):
            own(exclusive_owners, role.header, f"work_history[{role_index}].header")
            for index, candidate_id in enumerate(role.highlights):
                own(exclusive_owners, candidate_id, f"work_history[{role_index}].highlights[{index}]")
        for project_index, project in enumerate(layout.projects):
            for field_name in ("name", "description", "url"):
                candidate_id = getattr(project, field_name)
                if candidate_id is not None:
                    own(exclusive_owners, candidate_id, f"projects[{project_index}].{field_name}")
            for field_name, values in (
                ("technologies", project.technologies),
                ("achievements", project.achievements),
            ):
                for index, candidate_id in enumerate(values):
                    own(exclusive_owners, candidate_id, f"projects[{project_index}].{field_name}[{index}]")
        metadata_referenced = list(metadata_owners)
        exclusive_referenced = list(exclusive_owners)
        if not metadata_referenced and not exclusive_referenced and not allow_empty:
            raise _InvalidSourceReferenceError("empty_source_references", "source_references")
        candidate_aliases = {candidate.source_id: f"c{candidate.order}" for candidate in ledger.candidates}
        duplicate_diagnostics: list[InvalidReferenceDiagnostic] = []
        for candidate_id, paths in exclusive_owners.items():
            if len(paths) > 1:
                duplicate_diagnostics.extend(
                    InvalidReferenceDiagnostic(
                        "duplicate_exclusive_reference", path, candidate_aliases.get(candidate_id)
                    )
                    for path in paths
                )
        if duplicate_diagnostics:
            return _invalid_reference_result(*duplicate_diagnostics)
        overlap = set(metadata_owners) & set(exclusive_owners)
        if overlap:
            diagnostics = [
                InvalidReferenceDiagnostic(
                    "metadata_content_overlap", path, candidate_aliases.get(candidate_id)
                )
                for candidate_id in sorted(overlap, key=lambda item: candidate_aliases.get(item, ""))
                for path in (*metadata_owners[candidate_id], *exclusive_owners[candidate_id])
            ]
            return _invalid_reference_result(*diagnostics)
        return SourceLayoutResult(layout, "verified")
    except _InvalidSourceReferenceError as exc:
        return _invalid_reference_result(exc.diagnostic)
    except ValidationError as exc:
        return _invalid_reference_result(*_schema_validation_diagnostics(exc))
    except (TypeError, ValueError):
        return _invalid_reference_result(InvalidReferenceDiagnostic("schema_validation_failed", "source_references"))


def resolve_source_layout_v3(
    raw_layout: object,
    ledger: SourceResumeContentDocumentV2,
    artifacts: list[SourceResumeArtifact] | None = None,
) -> SourceLayoutResult:
    """Resolve model annotations and complete the ledger server-side."""
    if raw_layout is None:
        raw_layout = {}
    if not isinstance(raw_layout, dict):
        return SourceLayoutResult(None, "invalid", ("layout_object_expected",))
    allowed = {
        "contact", "links", "summary", "skills", "sections", "work_history",
        "projects", "education", "certifications", "licenses", "languages",
        "named_sections",
    }
    if set(raw_layout) - allowed:
        return SourceLayoutResult(None, "invalid", ("unknown_layout_field",))
    base = {
        key: value
        for key, value in raw_layout.items()
        if key in {
            "contact", "links", "summary", "skills", "sections", "work_history",
            "projects", "education", "certifications", "licenses", "languages",
        }
    }
    # Resolve the existing well-known sections first, then extend the resolved
    # role and named-section references with v3-only fields. An empty object is
    # a valid partial annotation; semantic viability is checked by the parser.
    base_roles = raw_layout.get("work_history", [])
    if isinstance(base_roles, list):
        allowed_role_fields = {
            "order", "header", "highlights", "context", "responsibilities_heading"
        }
        for role_index, role in enumerate(base_roles):
            if isinstance(role, dict) and set(role) - allowed_role_fields:
                return _invalid_reference_result(
                    InvalidReferenceDiagnostic(
                        "unknown_role_field", f"work_history[{role_index}]"
                    )
                )
        base["work_history"] = [
            {key: value for key, value in role.items() if key in {"order", "header", "highlights"}}
            for role in base_roles
            if isinstance(role, dict)
        ]
    if base:
        resolved = resolve_source_layout(
            base, ledger, normalize_order=True, allow_empty=True
        )
        if resolved.layout is None or resolved.status != "verified":
            return resolved
        resolved_data = resolved.layout.model_dump()
    else:
        resolved_data = SourceResumeLayoutReferences().model_dump()
    aliases = {f"c{candidate.order}": candidate.source_id for candidate in ledger.candidates}
    orders = {candidate.source_id: candidate.order for candidate in ledger.candidates}
    try:
        roles: list[SourceResumeRoleReferencesV3] = []
        for role_index, role in enumerate(base_roles if isinstance(base_roles, list) else []):
            if not isinstance(role, dict):
                raise _InvalidSourceReferenceError(
                    "role_object_expected", f"work_history[{role_index}]"
                )
            header = role.get("header")
            if not isinstance(header, str):
                raise _InvalidSourceReferenceError(
                    "role_header_expected", f"work_history[{role_index}].header"
                )
            role_index = len(roles)
            try:
                header_id = aliases[header]
            except KeyError:
                alias = header if re.fullmatch(r"c\d+", header) else None
                raise _InvalidSourceReferenceError(
                    "unknown_candidate_alias", f"work_history[{role_index}].header", alias
                ) from None
            context_value = role.get("context", [])
            if isinstance(context_value, str) or context_value is None:
                raise _InvalidSourceReferenceError(
                    "reference_list_expected", f"work_history[{role_index}].context"
                )
            context: list[UUID] = []
            for context_index, item in enumerate(context_value):
                if not isinstance(item, str) or item not in aliases:
                    alias = item if isinstance(item, str) and re.fullmatch(r"c\d+", item) else None
                    raise _InvalidSourceReferenceError(
                        "unknown_candidate_alias",
                        f"work_history[{role_index}].context[{context_index}]",
                        alias,
                    )
                context.append(aliases[item])
            responsibility = role.get("responsibilities_heading")
            if responsibility is not None and not isinstance(responsibility, str):
                raise _InvalidSourceReferenceError(
                    "responsibilities_heading_expected",
                    f"work_history[{role_index}].responsibilities_heading",
                )
            if responsibility and responsibility not in aliases:
                alias = responsibility if re.fullmatch(r"c\d+", responsibility) else None
                raise _InvalidSourceReferenceError(
                    "unknown_candidate_alias",
                    f"work_history[{role_index}].responsibilities_heading",
                    alias,
                )
            roles.append(
                SourceResumeRoleReferencesV3(
                    order=orders[header_id],
                    header=header_id,
                    highlights=sorted(
                        [aliases[item] for item in role.get("highlights", [])],
                        key=orders.__getitem__,
                    ),
                    context=sorted(context, key=orders.__getitem__),
                    responsibilities_heading=(aliases[responsibility] if responsibility else None),
                )
            )
        named: list[SourceResumeNamedSectionReferences] = []
        named_sections = raw_layout.get("named_sections", [])
        if isinstance(named_sections, str) or named_sections is None:
            raise _InvalidSourceReferenceError("reference_list_expected", "named_sections")
        for section_index, section in enumerate(named_sections):
            if not isinstance(section, dict):
                raise _InvalidSourceReferenceError(
                    "named_section_object_expected", f"named_sections[{section_index}]"
                )
            if set(section) - {"order", "heading", "items"}:
                raise _InvalidSourceReferenceError(
                    "unknown_named_section_field", f"named_sections[{section_index}]"
                )
            heading = section.get("heading")
            if heading is not None and (not isinstance(heading, str) or heading not in aliases):
                alias = heading if isinstance(heading, str) and re.fullmatch(r"c\d+", heading) else None
                raise _InvalidSourceReferenceError(
                    "unknown_candidate_alias", f"named_sections[{section_index}].heading", alias
                )
            items = section.get("items", [])
            if isinstance(items, str) or items is None:
                raise _InvalidSourceReferenceError(
                    "reference_list_expected", f"named_sections[{section_index}].items"
                )
            item_ids: list[UUID] = []
            for item_index, item in enumerate(items):
                if not isinstance(item, str) or item not in aliases:
                    alias = item if isinstance(item, str) and re.fullmatch(r"c\d+", item) else None
                    raise _InvalidSourceReferenceError(
                        "unknown_candidate_alias",
                        f"named_sections[{section_index}].items[{item_index}]",
                        alias,
                    )
                item_ids.append(aliases[item])
            named.append(
                SourceResumeNamedSectionReferences(
                    order=(orders[aliases[heading]] if heading else min((orders[item] for item in item_ids), default=0)),
                    heading=(aliases[heading] if heading else None),
                    items=sorted(item_ids, key=orders.__getitem__),
                )
            )
        resolved_data["work_history"] = roles
        resolved_data["named_sections"] = sorted(named, key=lambda section: section.order)
        resolved_projects: list[dict[str, object]] = []
        for project in resolved_data["projects"]:
            assert isinstance(project, dict)
            referenced = [
                value
                for value in (
                    project.get("name"), project.get("description"), project.get("url"),
                    *cast(list[UUID], project.get("technologies", [])),
                    *cast(list[UUID], project.get("achievements", [])),
                )
                if isinstance(value, UUID)
            ]
            project["order"] = min((orders[value] for value in referenced), default=len(orders))
            resolved_projects.append(project)
        resolved_data["projects"] = sorted(resolved_projects, key=lambda project: cast(int, project["order"]))
        layout = SourceResumeLayoutReferencesV3.model_validate(resolved_data)
        ownership: dict[UUID, list[str]] = {}

        def own(candidate_id: UUID, descriptor: str) -> None:
            ownership.setdefault(candidate_id, []).append(descriptor)

        candidate_aliases = {
            candidate.source_id: f"c{candidate.order}" for candidate in ledger.candidates
        }
        # A physical contact/link line may legitimately provide several
        # metadata fields. It remains one canonical metadata owner, while any
        # use outside that category is still rejected below.
        for field_name, candidate_id in layout.contact.items():
            own(candidate_id, f"contact.{field_name}")
        for field_name, candidate_id in layout.links.items():
            own(candidate_id, f"links.{field_name}")
        if layout.summary is not None:
            own(layout.summary, "summary")
        for index, candidate_id in enumerate(layout.skills):
            own(candidate_id, f"skills[{index}]")
        for index, candidate_id in enumerate(layout.sections):
            own(candidate_id, f"sections[{index}]")
        for index, candidate_id in enumerate(layout.education):
            own(candidate_id, f"education[{index}]")
        for index, candidate_id in enumerate(layout.certifications):
            own(candidate_id, f"certifications[{index}]")
        for index, candidate_id in enumerate(layout.licenses):
            own(candidate_id, f"licenses[{index}]")
        for index, candidate_id in enumerate(layout.languages):
            own(candidate_id, f"languages[{index}]")
        for role_index, role in enumerate(layout.work_history):
            own(role.header, f"work_history[{role_index}].header")
            for context_index, candidate_id in enumerate(role.context):
                own(candidate_id, f"work_history[{role_index}].context[{context_index}]")
            if role.responsibilities_heading is not None:
                own(role.responsibilities_heading, f"work_history[{role_index}].responsibilities_heading")
            for highlight_index, candidate_id in enumerate(role.highlights):
                own(candidate_id, f"work_history[{role_index}].highlights[{highlight_index}]")
        for project_index, project in enumerate(layout.projects):
            for field_name in ("name", "description", "url"):
                candidate_id = getattr(project, field_name)
                if candidate_id is not None:
                    own(candidate_id, f"projects[{project_index}].{field_name}")
            for technology_index, candidate_id in enumerate(project.technologies):
                own(candidate_id, f"projects[{project_index}].technologies[{technology_index}]")
            for achievement_index, candidate_id in enumerate(project.achievements):
                own(candidate_id, f"projects[{project_index}].achievements[{achievement_index}]")
        for section_index, section in enumerate(layout.named_sections):
            if section.heading is not None:
                own(section.heading, f"named_sections[{section_index}].heading")
            for item_index, candidate_id in enumerate(section.items):
                own(candidate_id, f"named_sections[{section_index}].items[{item_index}]")

        # The model may only contribute semantic ownership. Everything else
        # remains losslessly server-owned unless a deterministic artifact rule
        # applies.
        semantic_ids = set(ownership)
        artifact_ids = {artifact.source_id for artifact in artifacts or []}
        if semantic_ids & artifact_ids:
            return SourceLayoutResult(
                None, "invalid", ("duplicate_source_candidate_ownership",)
            )
        duplicate_diagnostics: list[InvalidReferenceDiagnostic] = []
        for candidate_id, owners in ownership.items():
            metadata = [path for path in owners if path.startswith(("contact.", "links."))]
            exclusive = [path for path in owners if path not in metadata]
            if len(exclusive) > 1 or (metadata and exclusive):
                reason = (
                    "duplicate_exclusive_reference"
                    if len(exclusive) > 1
                    else "metadata_content_overlap"
                )
                duplicate_diagnostics.extend(
                    InvalidReferenceDiagnostic(reason, path, candidate_aliases.get(candidate_id))
                    for path in owners
                )
        if duplicate_diagnostics:
            return SourceLayoutResult(
                None,
                "invalid",
                ("invalid_source_references",),
                invalid_reference_diagnostics=tuple(duplicate_diagnostics),
            )
        unclassified = [
            candidate.source_id
            for candidate in sorted(ledger.candidates, key=lambda candidate: candidate.order)
            if candidate.source_id not in semantic_ids and candidate.source_id not in artifact_ids
        ]
        layout = layout.model_copy(update={"unclassified": unclassified})
        for candidate_id in layout.unclassified:
            own(candidate_id, "unclassified")
        for artifact_index, artifact in enumerate(artifacts or []):
            own(artifact.source_id, f"artifact[{artifact_index}]:{artifact.reason}")

        candidate_ids = {candidate.source_id for candidate in ledger.candidates}
        if set(ownership) != candidate_ids:
            missing_candidate_orders = tuple(
                candidate.order
                for candidate in sorted(ledger.candidates, key=lambda candidate: candidate.order)
                if candidate.source_id not in ownership
            )
            return SourceLayoutResult(
                None,
                "invalid",
                ("incomplete_source_accounting",),
                missing_candidate_orders=missing_candidate_orders,
            )
        return SourceLayoutResult(layout, "verified")
    except _InvalidSourceReferenceError as exc:
        return _invalid_reference_result(exc.diagnostic)
    except ValidationError as exc:
        return _invalid_reference_result(*_schema_validation_diagnostics(exc))
    except (KeyError, TypeError, ValueError):
        return _invalid_reference_result(InvalidReferenceDiagnostic("schema_validation_failed", "source_references"))


def _annotate_section_range_conflicts(
    raw_layout: object,
    ledger: SourceResumeContentDocumentV2,
    result: SourceLayoutResult,
) -> SourceLayoutResult:
    """Annotate duplicate aliases assigned outside their detected section."""
    if result.status != "invalid" or not isinstance(raw_layout, dict):
        return result
    if not isinstance(raw_layout.get("section_headings"), dict):
        return result
    heading_names = {
        "professional summary": "summary", "summary": "summary",
        "technical stack": "skills", "technical skills": "skills",
        "professional experience": "work_history", "work experience": "work_history",
        "experience": "work_history", "education": "education",
        "certifications": "certifications", "projects": "projects",
        "licenses": "licenses", "languages": "languages",
    }
    detected = [
        (candidate.order, heading_names[candidate.text.casefold().strip()])
        for candidate in ledger.candidates
        if candidate.text.casefold().strip() in heading_names
    ]
    if not detected:
        return result
    alias_orders = {f"c{candidate.order}": candidate.order for candidate in ledger.candidates}
    expected_by_order: dict[int, str] = {}
    for index, (start, section) in enumerate(detected):
        end = detected[index + 1][0] if index + 1 < len(detected) else len(ledger.candidates)
        expected_by_order.update({order: section for order in range(start + 1, end)})
    body_roots = {
        "summary", "summary_lines", "skills", "sections", "work_history",
        "projects", "education", "certifications", "licenses", "languages",
        "named_sections",
    }
    diagnostics: list[InvalidReferenceDiagnostic] = []
    changed = False
    for diagnostic in result.invalid_reference_diagnostics:
        order = alias_orders.get(diagnostic.alias or "")
        expected = expected_by_order.get(order) if order is not None else None
        actual = diagnostic.path.split("[", 1)[0].split(".", 1)[0]
        if (
            diagnostic.reason == "duplicate_exclusive_reference"
            and expected is not None
            and actual in body_roots
            and actual != expected
        ):
            diagnostics.append(
                InvalidReferenceDiagnostic(
                    "section_range_conflict", diagnostic.path, diagnostic.alias, expected
                )
            )
            changed = True
        else:
            diagnostics.append(diagnostic)
    if not changed:
        return result
    return SourceLayoutResult(
        result.layout,
        result.status,
        result.errors,
        result.missing_candidate_orders,
        tuple(diagnostics),
    )


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
                # The verified header candidate is the durable role identity.
                # It is server-owned, stable across materialization, and keeps
                # duplicate/similar headers distinct through their UUIDs.
                source_id=role.header,
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


def materialize_source_document_v3(
    ledger: SourceResumeContentDocumentV3,
) -> SourceResumeDocument | None:
    """Materialize v3 content while retaining role context and source order."""
    if ledger.layout is None:
        return None
    by_id = {
        candidate.source_id: SourceTextItem.model_validate(candidate.model_dump())
        for candidate in ledger.candidates
    }
    layout = ledger.layout

    def item(candidate_id: UUID | None) -> SourceTextItem | None:
        return by_id.get(candidate_id) if candidate_id is not None else None

    roles = [
        SourceRole(
            source_id=role.header,
            order=role.order,
            company=item(role.header),
            title=item(role.header),
            context_items=[by_id[value] for value in role.context],
            responsibilities_heading=item(role.responsibilities_heading),
            highlights=[by_id[value] for value in role.highlights],
        )
        for role in sorted(layout.work_history, key=lambda value: value.order)
    ]
    section_order = [str(candidate.source_id) for candidate in ledger.candidates]
    return SourceResumeDocument(
        contact={key: by_id[value] for key, value in layout.contact.items()},
        links={key: by_id[value] for key, value in layout.links.items()},
        summary=item(layout.summary),
        skills=[by_id[value] for value in layout.skills],
        sections=[by_id[value] for value in layout.sections],
        work_history=roles,
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
        section_order=section_order,
        named_sections=[
            SourceNamedSection(
                source_id=section.heading or uuid4(),
                order=section.order,
                heading=item(section.heading),
                items=[by_id[value] for value in section.items],
            )
            for section in layout.named_sections
        ],
        unclassified=[by_id[value] for value in layout.unclassified],
    )


def _reconstruct_role_source_items(
    body: list[SourceResumeCandidate],
    responsibilities: str | None,
) -> tuple[list[str], list[str]]:
    """Classify one role body from source order without inventing membership.

    Bullet wraps are joined before this step. Leftover text after a heading or
    bullet is left unassigned so the resolver can preserve it as unclassified
    instead of promoting it to role prose.
    """
    bullet_items = [item for item in body if item.kind == "bullet"]
    if not bullet_items:
        # Plain-text extracts often lose list markers; retain those
        # role statements as selectable highlights rather than dropping
        # all tailoring semantics.
        return [], [
            f"c{item.order}"
            for item in body
            if f"c{item.order}" != responsibilities
        ]

    context: list[str] = []
    seen_boundary = False
    for item in body:
        alias = f"c{item.order}"
        if alias == responsibilities or item.kind in {"bullet", "heading"}:
            seen_boundary = True
            continue
        if not seen_boundary and item.kind == "text":
            context.append(alias)
    return context, [f"c{item.order}" for item in bullet_items]


def canonicalize_source_layout_v5(
    raw_layout: object,
    ledger: SourceResumeContentDocumentV2,
) -> object:
    """Constrain semantic selection to server-detected source section ranges.

    The selector still supplies header/contact semantics, but body ownership is
    deterministic. This prevents one model alias from becoming both a skill and
    a project/certification item, and allocates each work candidate exactly once.
    """
    if not isinstance(raw_layout, dict):
        return raw_layout
    candidates = {f"c{item.order}": item for item in ledger.candidates}
    ordered = sorted(ledger.candidates, key=lambda item: item.order)
    heading_names = {
        "professional summary": "summary", "summary": "summary",
        "technical stack": "skills", "technical skills": "skills",
        "professional experience": "work_history", "work experience": "work_history",
        "experience": "work_history", "education": "education",
        "certifications": "certifications", "projects": "projects",
        "licenses": "licenses", "languages": "languages",
    }
    detected: list[tuple[int, str]] = []
    named_index = 0
    for item in ordered:
        normalized = item.text.casefold().strip()
        if normalized in heading_names:
            detected.append((item.order, heading_names[normalized]))
        elif item.kind == "heading":
            detected.append((item.order, f"named:{named_index}"))
            named_index += 1
    if not detected:
        return raw_layout
    ranges: dict[str, list[str]] = {}
    for index, (start, section) in enumerate(detected):
        end = detected[index + 1][0] if index + 1 < len(detected) else len(ordered)
        ranges.setdefault(section, []).extend(
            f"c{item.order}" for item in ordered if start < item.order < end
        )
    headings = {section: f"c{order}" for order, section in detected}
    result = dict(raw_layout)
    result["section_headings"] = headings
    result["sections"] = []

    summary_body = ranges.get("summary", [])
    result["summary"] = summary_body[0] if summary_body else None
    result["summary_lines"] = summary_body[1:]
    result["skills"] = ranges.get("skills", [])
    for section in ("education", "certifications", "licenses", "languages"):
        result[section] = ranges.get(section, [])

    project_body = ranges.get("projects", [])
    result["projects"] = (
        [{"order": 0, "name": None, "description": None, "url": None,
          "technologies": [], "achievements": project_body}]
        if project_body else []
    )

    work_body = ranges.get("work_history", [])
    work_start = next((order for order, section in detected if section == "work_history"), None)
    if work_start is not None and isinstance(raw_layout.get("work_history"), list):
        header_aliases: list[str] = []
        for role in raw_layout["work_history"]:
            if not isinstance(role, dict):
                continue
            alias = role.get("header")
            if isinstance(alias, str) and alias in candidates and alias in work_body:
                header_aliases.append(alias)
        header_aliases = sorted(set(header_aliases), key=lambda alias: candidates[alias].order)
        rebuilt_roles: list[dict[str, object]] = []
        for index, alias in enumerate(header_aliases):
            start = candidates[alias].order
            end = (
                candidates[header_aliases[index + 1]].order
                if index + 1 < len(header_aliases)
                else len(ordered)
            )
            body = [
                item for item in ordered
                if start < item.order < end and f"c{item.order}" in work_body
            ]
            responsibilities = next(
                (
                    f"c{item.order}"
                    for item in body
                    if "responsibilit" in item.text.casefold()
                    and item.kind != "bullet"
                ),
                None,
            )
            context_aliases, highlight_aliases = _reconstruct_role_source_items(
                body, responsibilities
            )
            rebuilt_roles.append({
                "order": index,
                "header": alias,
                "context": context_aliases,
                "responsibilities_heading": responsibilities,
                "highlights": highlight_aliases,
            })
        result["work_history"] = rebuilt_roles

    named_sections = []
    for order, section in detected:
        if not section.startswith("named:"):
            continue
        items = ranges.get(section, [])
        named_sections.append({
            "order": order,
            "heading": f"c{order}",
            "items": items,
        })
    result["named_sections"] = named_sections
    return result


def resolve_source_layout_v4(
    raw_layout: object,
    ledger: SourceResumeContentDocumentV2,
    artifacts: list[SourceResumeArtifact] | None = None,
) -> SourceLayoutResult:
    """Resolve v4 additions through the proven v3 ownership verifier.

    Wrapped summary lines and dedicated headings remain separately addressable in
    the persisted layout, but are temporarily treated as ordinary sections while
    the v3 verifier proves every candidate has exactly one owner.
    """
    if not isinstance(raw_layout, dict):
        return SourceLayoutResult(None, "invalid", ("layout_object_expected",))
    allowed = {
        "contact", "links", "summary", "summary_lines", "section_headings",
        "skills", "sections", "work_history", "projects", "education",
        "certifications", "licenses", "languages", "named_sections",
    }
    if set(raw_layout) - allowed:
        return SourceLayoutResult(None, "invalid", ("unknown_layout_field",))
    summary_lines = raw_layout.get("summary_lines", [])
    headings = raw_layout.get("section_headings", {})
    if not isinstance(summary_lines, list) or not isinstance(headings, dict):
        return SourceLayoutResult(None, "invalid", ("invalid_source_references",))
    allowed_heading_keys = {
        "summary", "skills", "work_history", "education", "certifications",
        "projects", "licenses", "languages",
    }
    if any(
        not isinstance(key, str)
        or (
            key not in allowed_heading_keys
            and not re.fullmatch(r"named:\d+", key)
        )
        for key in headings
    ):
        return SourceLayoutResult(None, "invalid", ("unknown_section_heading",))
    # The strict selector schema requires every supported heading key to be
    # present, using null for headings that are absent in this document. Null
    # is not a source candidate and must not be passed into the v3 resolver.
    headings = {key: value for key, value in headings.items() if value is not None}
    transformed = dict(raw_layout)
    transformed.pop("summary_lines", None)
    transformed.pop("section_headings", None)
    named_sections = transformed.get("named_sections", [])
    if not isinstance(named_sections, list):
        return SourceLayoutResult(None, "invalid", ("invalid_source_references",))
    named_sections = [dict(section) if isinstance(section, dict) else section for section in named_sections]
    for key, alias in headings.items():
        if not key.startswith("named:"):
            continue
        index = int(key.split(":", 1)[1])
        if index >= len(named_sections) or not isinstance(named_sections[index], dict):
            return SourceLayoutResult(None, "invalid", ("invalid_source_references",))
        existing = named_sections[index].get("heading")
        if existing is not None and existing != alias:
            return SourceLayoutResult(None, "invalid", ("duplicate_source_candidate_ownership",))
        named_sections[index]["heading"] = alias
    transformed["named_sections"] = named_sections
    transformed["sections"] = [
        *summary_lines,
        *[alias for key, alias in headings.items() if not key.startswith("named:")],
        *list(raw_layout.get("sections", [])),
    ]
    result = resolve_source_layout_v3(transformed, ledger, artifacts)
    result = _annotate_section_range_conflicts(raw_layout, ledger, result)
    if result.status != "verified" or result.layout is None:
        return result
    layout = result.layout
    aliases = {f"c{candidate.order}": candidate.source_id for candidate in ledger.candidates}
    orders = {candidate.source_id: candidate.order for candidate in ledger.candidates}
    try:
        resolved_summary_lines = sorted(
            (aliases[value] for value in summary_lines), key=orders.__getitem__
        )
        resolved_headings = {key: aliases[value] for key, value in headings.items()}
        if layout.summary is not None and any(
            orders[value] < orders[layout.summary] for value in resolved_summary_lines
        ):
            return SourceLayoutResult(None, "invalid", ("summary_order_invalid",))
    except (KeyError, TypeError):
        return SourceLayoutResult(None, "invalid", ("invalid_source_references",))
    excluded = set(resolved_summary_lines) | set(resolved_headings.values())
    resolved = SourceResumeLayoutReferencesV4.model_validate({
        **layout.model_dump(),
        "summary": layout.summary,
        "summary_lines": resolved_summary_lines,
        "section_headings": resolved_headings,
        "sections": [value for value in layout.sections if value not in excluded],
    })
    return SourceLayoutResult(resolved, "verified")


def materialize_source_document_v4(
    ledger: SourceResumeContentDocumentV4,
) -> SourceResumeDocument | None:
    """Materialize v4 while retaining wrapped summary and source headings."""
    if ledger.layout is None:
        return None
    base = materialize_source_document_v3(
        SourceResumeContentDocumentV3(
            candidates=ledger.candidates,
            layout=SourceResumeLayoutReferencesV3.model_validate(
                ledger.layout.model_dump(exclude={"summary_lines", "section_headings"})
            ),
            artifacts=ledger.artifacts,
        )
    )
    if base is None:
        return None
    by_id = {candidate.source_id: SourceTextItem.model_validate(candidate.model_dump()) for candidate in ledger.candidates}
    return base.model_copy(update={
        "summary_lines": [by_id[value] for value in ledger.layout.summary_lines],
        "section_headings": {key: by_id[value] for key, value in ledger.layout.section_headings.items()},
    })


def has_usable_tailoring_semantics(
    layout: SourceResumeLayoutReferences | None,
) -> bool:
    """Return whether semantic annotations can drive source-backed tailoring."""
    return bool(layout and any(role.highlights for role in layout.work_history))


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
