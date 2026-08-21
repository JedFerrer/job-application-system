# ruff: noqa: E501
"""Resume domain service: orchestrates parse -> embed -> persist.

Pure-ish business logic; no FastAPI/HTTP concerns. Raises domain exceptions.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

from app.core.logging import get_logger
from app.domain.jobs.exceptions import JobNotFoundError
from app.domain.jobs.repository import JobRepository
from app.domain.matching.skill_matcher import (
    SkillMatchReport,
    extract_jd_skills,
    extract_known_skills,
    normalize_skill,
)
from app.domain.resume.completeness import find_completeness_warnings
from app.domain.resume.exceptions import (
    ProfileNotFoundError,
    ResumeImportDraftNotFoundError,
    ResumeParseError,
    ResumeSourceLayoutUnavailableError,
    ResumeTextTooLongError,
    TailoredResumeDraftNotFoundError,
    TailoredResumeSnapshotInvalidError,
    UnsupportedResumeFormatError,
)
from app.domain.resume.lifecycle import ResumeLifecycleStatus
from app.domain.resume.matching_inputs import build_matching_input_fingerprint
from app.domain.resume.prompts import TAILOR_RESUME_PROMPT
from app.domain.resume.provenance import (
    ResumeImportProvenance,
    build_resume_import_provenance,
)
from app.domain.resume.repository import ResumeRepository
from app.domain.resume.requirement_classification import (
    classify_requirements,
    classify_resume_coverage,
)
from app.domain.resume.schemas import (
    ExtractionEvidence,
    ParsedProfile,
    ParsedResumeResult,
    ProfileResponse,
    RequirementClassification,
    ResumeImportDetail,
    ResumeImportListItem,
    ResumeResponse,
    SourceResumeContentPayloadV4,
    SourceResumeDocument,
    TailoredBulletDecision,
    TailoredCanonicalItem,
    TailoredCanonicalSection,
    TailoredResume,
    TailoredResumeDraftCreate,
    TailoredResumeDraftListItem,
    TailoredResumeDraftResponse,
    TailoredWorkItem,
)
from app.domain.resume.selection import (
    select_legacy_source_bullets,
    select_source_bullets,
)
from app.domain.resume.source_content import (
    materialize_source_document_v4,
    source_revision_sha256,
)
from app.domain.resume.truthfulness import (
    build_evidence_catalog,
    build_source_evidence_catalog,
    sanitize_tailoring_output,
)
from app.infrastructure.embeddings import embed_text
from app.infrastructure.llm_client import llm_chat_completion
from app.infrastructure.llm_utils import strip_code_fences
from app.infrastructure.resume_parser import (
    MAX_RESUME_TEXT_CHARS,
    extract_text_from_bytes,
    parse_resume_text,
)

logger = get_logger("resume_service")
FULL_RESUME_SNAPSHOT_VERSION = "full-resume-v1"

ProgressFn = Callable[..., Awaitable[None]]


def _join_wrapped_source(items: list[Any]) -> str | None:
    """Join PDF soft-wrap candidates without changing their source provenance."""
    values = [str(item.text).strip() for item in items if item is not None and item.text.strip()]
    if not values:
        return None
    joined = values[0]
    for value in values[1:]:
        joined += value if joined.endswith("-") else f" {value}"
    return joined


def _canonical_sections(source_document: Any) -> list[TailoredCanonicalSection]:
    """Project verified source text into an ordered snapshot view."""
    if source_document is None:
        return []

    def items(values: list[Any]) -> list[TailoredCanonicalItem]:
        return [
            TailoredCanonicalItem(
                source_id=value.source_id,
                text=value.text,
                canonical_order=value.order,
            )
            for value in sorted(values, key=lambda value: value.order)
        ]

    sections: list[TailoredCanonicalSection] = []
    contact = items(list(source_document.contact.values()))
    links = items(list(source_document.links.values()))
    if contact:
        sections.append(TailoredCanonicalSection(section="contact", items=contact, canonical_order=min(i.canonical_order for i in contact)))
    if links:
        sections.append(TailoredCanonicalSection(section="links", items=links, canonical_order=min(i.canonical_order for i in links)))
    summary_values = [value for value in [source_document.summary, *source_document.summary_lines] if value is not None]
    if summary_values:
        sections.append(
            TailoredCanonicalSection(
                section="summary",
                heading=(source_document.section_headings.get("summary").text if source_document.section_headings.get("summary") else None),
                items=items(summary_values),
                canonical_order=summary_values[0].order,
            )
        )
    for section_name, values in (
        ("skills", source_document.skills),
        ("education", source_document.education),
        ("certifications", source_document.certifications),
        ("licenses", source_document.licenses),
        ("languages", source_document.languages),
    ):
        section_items = items(values)
        if section_items:
            sections.append(
                TailoredCanonicalSection(
                    section=section_name,
                    heading=(source_document.section_headings.get(section_name).text if source_document.section_headings.get(section_name) else None),
                    items=section_items,
                    canonical_order=min(i.canonical_order for i in section_items),
                )
            )
    project_values: list[Any] = []
    for project in source_document.projects:
        project_values.extend(
            value
            for value in (
                project.name,
                project.description,
                *project.technologies,
                *project.achievements,
                project.url,
            )
            if value is not None
        )
    project_items = items(project_values)
    if project_items:
        projects_heading = source_document.section_headings.get("projects")
        sections.append(
            TailoredCanonicalSection(
                section="projects",
                heading=projects_heading.text if projects_heading else None,
                items=project_items,
                canonical_order=min(i.canonical_order for i in project_items),
            )
        )
    for named in getattr(source_document, "named_sections", []):
        section_items = items(named.items)
        if section_items:
            sections.append(TailoredCanonicalSection(section="named", heading=named.heading.text if named.heading else None, items=section_items, canonical_order=named.order))
    run: list[Any] = []
    previous_order: int | None = None
    for value in sorted(getattr(source_document, "unclassified", []), key=lambda value: value.order):
        if run and previous_order is not None and value.order != previous_order + 1:
            run_items = items(run)
            sections.append(
                TailoredCanonicalSection(
                    section="unclassified",
                    items=run_items,
                    canonical_order=run_items[0].canonical_order,
                )
            )
            run = []
        run.append(value)
        previous_order = value.order
    if run:
        run_items = items(run)
        sections.append(
            TailoredCanonicalSection(
                section="unclassified",
                items=run_items,
                canonical_order=run_items[0].canonical_order,
            )
        )
    return sorted(sections, key=lambda section: section.canonical_order)


def _source_output_orders(
    source_roles: list[Any], recommended_ids: list[list[str]]
) -> list[list[str]]:
    """Return recommended-first output order while retaining every source bullet."""
    output: list[list[str]] = []
    for role, recommended in zip(source_roles, recommended_ids, strict=True):
        recommended_set = set(recommended)
        all_ids = [str(bullet.source_id) for bullet in role.highlights]
        output.append(
            recommended
            + [source_id for source_id in all_ids if source_id not in recommended_set]
        )
    return output


def _validate_source_backed_snapshot(
    resume: TailoredResume,
    *,
    source_document: SourceResumeDocument,
    profile: ParsedProfile,
    job_description: str | None,
) -> None:
    """Validate a reviewed fresh snapshot against the owned canonical source.

    This is intentionally an invariant check, not a recommendation pass: the
    applicant's final inclusion, wording, and ordering choices are preserved.
    """

    def invalid() -> None:
        raise TailoredResumeSnapshotInvalidError()

    if resume.canonical_sections != _canonical_sections(source_document):
        invalid()
    expected_summary = source_document.summary.text if source_document.summary else None
    if resume.professional_summary != expected_summary:
        invalid()
    if resume.full_name is not None or resume.email is not None or resume.phone is not None:
        invalid()
    if resume.headline is not None or resume.links.model_dump(exclude_none=True):
        invalid()
    if resume.skills != [item.text for item in source_document.skills]:
        invalid()
    if (
        resume.projects
        or resume.languages
        or resume.licenses
        or resume.education
        or resume.certifications
    ):
        invalid()

    roles = source_document.work_history
    if len(resume.work_history) != len(roles):
        invalid()

    missing_skills = [
        item.requirement
        for item in classify_requirements(job_description, profile)
        if item.status == "missing"
    ]
    selection = select_source_bullets(roles, job_description or "")
    legacy = resume.tailoring_snapshot_version is None
    if resume.tailoring_snapshot_version not in {None, FULL_RESUME_SNAPSHOT_VERSION, "lossless-complete-v2"}:
        invalid()
    if legacy:
        recommended_ids = select_legacy_source_bullets(roles, job_description or "")
        rewrite_ids = recommended_ids
        output_orders = recommended_ids
    else:
        recommended_ids = selection.recommended_ids
        rewrite_ids = selection.rewrite_candidate_ids
        output_orders = _source_output_orders(roles, recommended_ids)
    catalog = build_source_evidence_catalog(roles)
    eligible = {index: set(ids) for index, ids in enumerate(rewrite_ids)}
    orders = [list(ids) for ids in rewrite_ids]
    relevance = selection.scores

    for role_index, (submitted, source_role) in enumerate(
        zip(resume.work_history, roles, strict=True)
    ):
        if submitted.source_role_id != source_role.source_id:
            invalid()
        for field in ("company", "title", "start", "end"):
            source_item = getattr(source_role, field)
            expected = source_item.text if source_item is not None else None
            if getattr(submitted, field) != expected:
                invalid()
        if submitted.source_header != (source_role.company.text if source_role.company else None):
            invalid()
        if submitted.role_context != [item.text for item in source_role.context_items]:
            invalid()
        expected_responsibilities_heading = (
            source_role.responsibilities_heading.text
            if source_role.responsibilities_heading is not None
            else None
        )
        if submitted.responsibilities_heading != expected_responsibilities_heading:
            invalid()
        source_texts = [item.text for item in source_role.highlights]
        if submitted.original_highlights != source_texts:
            invalid()
        decisions = submitted.selection_decisions
        if len(decisions) != len(source_role.highlights):
            invalid()

        recommended_for_role = set(recommended_ids[role_index])
        suggestion_by_index: dict[int, str] = {}
        for source_index, decision in enumerate(decisions):
            source_item = source_role.highlights[source_index]
            source_id = str(source_item.source_id)
            expected_rank = (
                recommended_ids[role_index].index(source_id)
                if source_id in recommended_for_role
                else None
            )
            if (
                decision.source_id != source_item.source_id
                or decision.source_index != source_index
                or decision.canonical_order != source_item.order
                or decision.original_text != source_item.text
                or decision.recommended_included != (source_id in recommended_for_role)
                or decision.recommendation_rank != expected_rank
            ):
                invalid()
            if decision.suggested_text != source_item.text:
                if source_id not in set(rewrite_ids[role_index]):
                    invalid()
                sanitized = sanitize_tailoring_output(
                    profile,
                    {
                        "rewrites": [
                            {
                                "target_alias": f"r{role_index}:b{source_index}",
                                "evidence_aliases": [f"r{role_index}:b{source_index}"],
                                "rewritten_text": decision.suggested_text,
                            }
                        ]
                    },
                    missing_skills,
                    eligible,
                    orders,
                    catalog,
                    relevance,
                    roles,
                    output_orders,
                )
                expected_suggestion = sanitized.tailored_highlights[role_index][
                    output_orders[role_index].index(source_id)
                ]
                if expected_suggestion != decision.suggested_text:
                    invalid()
            if decision.final_text not in {decision.original_text, decision.suggested_text}:
                invalid()
            suggestion_by_index[source_index] = decision.final_text

        included_indices = [
            index for index, decision in enumerate(decisions) if decision.final_included
        ]
        included_indices.sort(
            key=lambda index: (
                not decisions[index].recommended_included,
                decisions[index].recommendation_rank
                if decisions[index].recommended_included
                else decisions[index].canonical_order,
            )
        )
        if submitted.tailored_highlight_source_indices != included_indices:
            invalid()
        if submitted.tailored_highlights != [
            suggestion_by_index[index] for index in included_indices
        ]:
            invalid()
        output_ranks = {
            index: rank for rank, index in enumerate(included_indices)
        }
        for index, decision in enumerate(decisions):
            if decision.output_rank != output_ranks.get(index):
                invalid()


def build_summary_text(profile: ParsedProfile) -> str:
    """Build the canonical text used to embed a profile for job matching.

    Concentrates the signal LLM matching cares about: headline, skills,
    preferred roles, and work-history highlights.
    """
    parts: list[str] = []
    if profile.headline:
        parts.append(profile.headline)
    if profile.skills:
        parts.append("Skills: " + ", ".join(profile.skills))
    if profile.preferred_roles:
        parts.append("Preferred roles: " + ", ".join(profile.preferred_roles))
    if profile.years_experience is not None:
        parts.append(f"Experience: {profile.years_experience} years")
    for item in profile.work_history:
        header = " ".join(filter(None, [item.title, "at", item.company]))
        if header.strip():
            parts.append(header)
        parts.extend(item.highlights)
    return "\n".join(p for p in parts if p).strip() or "No profile content"


def _untailored_fallback(
    profile: ParsedProfile,
    job_id: int,
    report: SkillMatchReport,
    requirement_classifications: list[RequirementClassification] | None = None,
    bullet_orders: list[list[str]] | None = None,
    recommended_orders: list[list[str]] | None = None,
    source_roles: list[Any] | None = None,
    canonical_sections: list[TailoredCanonicalSection] | None = None,
    source_document: SourceResumeDocument | None = None,
    source_resume_id: int | None = None,
    source_resume_content_id: int | None = None,
    source_revision: str | None = None,
) -> TailoredResume:
    """Return the profile as-is when LLM tailoring is unavailable."""
    matched_set = set(report.matched_skills)
    other_skills = [s for s in profile.skills if normalize_skill(s) not in matched_set]
    roles = source_roles if source_roles is not None else profile.work_history
    orders = bullet_orders or [
        [
            str(bullet.source_id)
            if source_roles is not None
            else f"profile-bullet:{role_index}:{source_index}"
            for source_index, bullet in enumerate(w.highlights)
        ]
        for role_index, w in enumerate(roles)
    ]
    recommendations = recommended_orders
    if recommendations is None and source_roles is not None:
        recommendations = select_source_bullets(source_roles, "").recommended_ids
    recommendations = recommendations or [[] for _ in roles]
    source_indices = [
        [
            next(
                index
                for index, bullet in enumerate(role.highlights)
                if (
                    str(bullet.source_id) == source_uuid
                    if source_roles is not None
                    else f"profile-bullet:{role_index}:{index}" == source_uuid
                )
            )
            for source_uuid in role_order
        ]
        for role_index, (role, role_order) in enumerate(zip(roles, orders, strict=True))
    ]
    source_backed = source_document is not None and canonical_sections is not None
    source_skills = (
        [item.text for item in source_document.skills]
        if source_document is not None
        else list(profile.skills)
    )
    source_summary = (
        _join_wrapped_source([source_document.summary, *source_document.summary_lines])
        if source_document is not None
        else None
    )
    resume = TailoredResume(
        job_id=job_id,
        professional_summary=source_summary if source_backed else (profile.headline or None),
        full_name=None if source_backed else profile.full_name,
        email=None if source_backed else profile.email,
        phone=None if source_backed else profile.phone,
        headline=None if source_backed else profile.headline,
        skills=source_skills,
        links=profile.links.model_copy(deep=True) if not source_backed else {},
        projects=(
            [item.model_copy(deep=True) for item in profile.projects]
            if not source_backed
            else []
        ),
        languages=list(profile.languages) if not source_backed else [],
        licenses=list(profile.licenses) if not source_backed else [],
        education=list(profile.education) if not source_backed else [],
        certifications=list(profile.certifications) if not source_backed else [],
        work_history=[
            TailoredWorkItem(
                company=w.company.text if source_roles is not None and w.company else w.company,
                title=w.title.text if source_roles is not None and w.title else w.title,
                start=w.start.text if source_roles is not None and w.start else w.start,
                end=w.end.text if source_roles is not None and w.end else w.end,
                original_highlights=(
                    [b.text for b in w.highlights]
                    if source_roles is not None
                    else list(w.highlights)
                ),
                tailored_highlights=[
                    (w.highlights[index].text if source_roles is not None else w.highlights[index])
                    for index in source_indices[index_role]
                ],
                tailored_highlight_source_indices=source_indices[index_role],
                source_role_id=w.source_id if source_roles is not None else None,
                source_header=(
                    w.company.text
                    if source_roles is not None and w.company
                    else None
                ),
                role_context=(
                    [item.text for item in w.context_items]
                    if source_roles is not None
                    else []
                ),
                responsibilities_heading=(
                    w.responsibilities_heading.text
                    if source_roles is not None and w.responsibilities_heading
                    else None
                ),
                selection_decisions=(
                    _selection_decisions(
                        w,
                        recommendations[index_role],
                        orders[index_role],
                        [
                            w.highlights[index].text
                            for index in source_indices[index_role]
                        ],
                    )
                    if source_roles is not None
                    else []
                ),
            )
            for index_role, w in enumerate(roles)
        ],
        matched_skills=report.matched_skills,
        other_skills=other_skills,
        missing_keywords=report.missing_skills,
        requirement_classifications=requirement_classifications or [],
        tailoring_snapshot_version=("lossless-complete-v2" if source_backed else None),
        canonical_sections=canonical_sections or [],
        source_resume_id=source_resume_id if source_backed else None,
        source_resume_content_id=source_resume_content_id if source_backed else None,
        source_schema_version="source-resume-v4" if source_backed else None,
        source_revision_sha256=source_revision if source_backed else None,
    )
    resume.requirement_classifications = classify_resume_coverage(
        resume.requirement_classifications, resume
    )
    return resume


def _bullet_relevance(
    profile: ParsedProfile,
    job_description: str,
    source_roles: list[Any] | None = None,
    selected_ids: list[list[str]] | None = None,
) -> tuple[list[list[str]], dict[int, set[str]], list[list[dict[str, object]]]]:
    """Return stable per-role ordering and the source bullets eligible for rewriting."""
    jd_skills = set(extract_jd_skills(job_description))
    orders: list[list[str]] = []
    eligible: dict[int, set[str]] = {}
    prompt_roles: list[list[dict[str, object]]] = []
    roles = source_roles if source_roles is not None else profile.work_history
    for role_index, role in enumerate(roles):
        allowed = set(selected_ids[role_index]) if selected_ids is not None else None
        scored: list[tuple[int, int, list[str]]] = []
        for source_index, bullet in enumerate(role.highlights):
            source_id = (
                str(bullet.source_id)
                if source_roles is not None
                else f"profile-bullet:{role_index}:{source_index}"
            )
            if allowed is not None and source_id not in allowed:
                continue
            text = bullet.text if source_roles is not None else bullet
            bullet_skills = sorted(jd_skills & set(extract_known_skills(text)))
            scored.append((len(bullet_skills), source_index, bullet_skills))
        ordered = sorted(scored, key=lambda item: (-item[0], item[1]))
        orders.append(
            [
                str(role.highlights[source_index].source_id)
                if source_roles is not None
                else f"profile-bullet:{role_index}:{source_index}"
                for _, source_index, _ in ordered
            ]
        )
        eligible[role_index] = {
            str(role.highlights[source_index].source_id)
            if source_roles is not None
            else f"profile-bullet:{role_index}:{source_index}"
            for score, source_index, _ in scored
            if score > 0
        }
        same_role_evidence = [
            {
                "evidence_id": f"r{role_index}:b{source_index}",
                "target_alias": f"r{role_index}:b{source_index}",
                "source_uuid": str(role.highlights[source_index].source_id)
                if source_roles is not None
                else f"profile-bullet:{role_index}:{source_index}",
                "source_index": source_index,
                "source_text": (
                    role.highlights[source_index].text
                    if source_roles is not None
                    else role.highlights[source_index]
                ),
            }
            for source_index in range(len(role.highlights))
            if allowed is None
            or source_roles is None
            or str(role.highlights[source_index].source_id) in allowed
        ]
        prompt_roles.append([
            {
                "role_index": role_index,
                "source_index": source_index,
                "source_text": (
                    role.highlights[source_index].text
                    if source_roles is not None
                    else role.highlights[source_index]
                ),
                "evidence_id": f"r{role_index}:b{source_index}",
                "target_alias": f"r{role_index}:b{source_index}",
                "source_uuid": str(role.highlights[source_index].source_id)
                if source_roles is not None
                else f"profile-bullet:{role_index}:{source_index}",
                "relevance_score": score,
                "matched_skills": skills,
                "same_role_evidence": same_role_evidence,
            }
            for score, source_index, skills in scored
            if score > 0
        ])
    return orders, eligible, prompt_roles


def _selection_decisions(
    role: Any,
    recommended_ids: list[str],
    output_ids: list[str],
    output_text: list[str],
) -> list[Any]:
    """Build UUID-traceable recommendation state for one canonical role."""
    output_text_by_id = dict(zip(output_ids, output_text, strict=True))
    recommendation_rank = {
        source_id: rank for rank, source_id in enumerate(recommended_ids)
    }
    output_rank = {source_id: rank for rank, source_id in enumerate(output_ids)}
    return [
        TailoredBulletDecision(
            source_id=bullet.source_id,
            source_index=index,
            canonical_order=bullet.order,
            original_text=bullet.text,
            suggested_text=output_text_by_id.get(str(bullet.source_id), bullet.text),
            final_text=output_text_by_id.get(str(bullet.source_id), bullet.text),
            recommended_included=str(bullet.source_id) in recommendation_rank,
            final_included=True,
            recommendation_rank=recommendation_rank.get(str(bullet.source_id)),
            output_rank=output_rank.get(str(bullet.source_id)),
        )
        for index, bullet in enumerate(role.highlights)
    ]


class ResumeService:
    def __init__(self, repository: ResumeRepository) -> None:
        self._repository = repository

    async def upload_and_parse(
        self, *, user_id: int, filename: str, content: bytes,
        progress: ProgressFn | None = None,
    ) -> ResumeResponse:
        """Extract text, parse, and persist an active profile or import draft."""

        async def _emit(**kw: Any) -> None:
            if progress is not None:
                await progress(**kw)

        await _emit(status="running", message="Extracting text…")
        try:
            extracted = extract_text_from_bytes(content, filename, True)
            if isinstance(extracted, str):
                raw_text = extracted
                extraction_warnings = []
            else:
                raw_text = extracted.text
                extraction_warnings = list(extracted.warnings)
        except ValueError as exc:
            raise UnsupportedResumeFormatError(str(exc)) from exc

        if len(raw_text) > MAX_RESUME_TEXT_CHARS:
            raise ResumeTextTooLongError(len(raw_text), MAX_RESUME_TEXT_CHARS)

        await _emit(message="Parsing with AI…")
        try:
            parsed_result = await parse_resume_text(raw_text)
        except ValueError as exc:
            raise ResumeParseError(str(exc)) from exc
        if isinstance(parsed_result, ParsedResumeResult):
            profile = parsed_result.profile
            extraction_evidence = parsed_result.extraction_evidence
            source_content = parsed_result.source_content
            source_validation_status = cast(
                str | None, parsed_result.source_validation_status
            )
            source_validation_errors = list(parsed_result.source_validation_errors)
            if (
                source_validation_status is None
                and not hasattr(self._repository, "add_source_content")
            ):
                source_validation_status = "legacy-test-seam"
        else:
            # Legacy focused test seams may still return only a profile. The
            # production repository exposes source persistence, so a real
            # import with this shape fails closed below rather than creating a
            # source-less supported resume.
            if hasattr(self._repository, "add_source_content"):
                raise ResumeParseError(
                    "Resume source extraction was missing; the resume was not imported."
                )
            profile = parsed_result
            extraction_evidence = ExtractionEvidence()
            source_content = None
            source_validation_status = "legacy-test-seam"
            source_validation_errors = []
        if source_validation_status not in {"verified", "legacy-test-seam"} or (
            source_validation_status == "verified" and source_content is None
        ):
            details = ", ".join(source_validation_errors) or "source content could not be verified"
            raise ResumeParseError(
                f"Resume source extraction failed verification: {details}"
            )
        extraction_warning_codes = [warning.code for warning in extraction_warnings]
        response_warnings = list(extraction_warnings)
        response_warnings.extend(find_completeness_warnings(raw_text, profile))
        provenance = build_resume_import_provenance(
            filename, extraction_warning_codes
        )

        await _emit(message="Embedding profile…")
        summary_text = build_summary_text(profile)
        await self._repository.lock_user_for_import(user_id)
        previous_profile = await self._repository.get_active_profile(user_id)
        is_first_import = previous_profile is None
        if is_first_import:
            embedding_input_changed = True
        else:
            assert previous_profile is not None
            embedding_input_changed = summary_text != previous_profile.summary_text
        if embedding_input_changed or (
            not is_first_import
            and previous_profile is not None
            and previous_profile.embedding is None
        ):
            embedding = await embed_text(summary_text)
        else:
            assert previous_profile is not None
            embedding = previous_profile.embedding

        # Only a server-verified source-resume-v4 can become active. Existing
        # v3 snapshots must be re-imported instead of entering a half-supported
        # active state.
        source_ready = source_validation_status == "verified" and isinstance(
            source_content, SourceResumeContentPayloadV4
        )
        lifecycle_status = (
            ResumeLifecycleStatus.ACTIVE
            if is_first_import and source_ready
            else ResumeLifecycleStatus.DRAFT
        )
        is_active = lifecycle_status == ResumeLifecycleStatus.ACTIVE
        resume = await self._repository.add_resume(
            user_id=user_id,
            filename=filename,
            raw_text=raw_text,
            is_active=is_active,
            lifecycle_status=lifecycle_status,
            import_provenance=provenance.model_dump(mode="json"),
        )
        if source_validation_status == "verified":
            await self._repository.add_source_content(
                resume_id=resume.id,
                content=(
                    source_content.model_dump(mode="json")
                    if source_content is not None
                    else {}
                ),
                validation_status=source_validation_status,
                validation_errors=source_validation_errors,
                schema_version=source_content.schema_version
                if source_content is not None
                else "source-resume-v2",
            )
        await self._repository.add_profile(
            user_id=user_id,
            resume_id=resume.id,
            parsed=profile.model_dump(),
            summary_text=summary_text,
            embedding=embedding,
            extraction_evidence=extraction_evidence.model_dump(mode="json"),
        )
        logger.info("resume_parsed", user_id=user_id, resume_id=resume.id)
        return ResumeResponse(
            resume_id=resume.id,
            filename=resume.filename,
            is_active=resume.is_active,
            lifecycle_status=ResumeLifecycleStatus(resume.lifecycle_status),
            profile=profile,
            matching_profile_changed=is_active,
            extraction_warnings=response_warnings,
            provenance=provenance,
        )

    async def get_profile(self, user_id: int) -> ProfileResponse:
        profile_row = await self._repository.get_active_profile(user_id)
        if profile_row is None:
            raise ProfileNotFoundError(user_id)
        return ProfileResponse(
            profile_id=profile_row.id,
            resume_id=profile_row.resume_id,
            profile=ParsedProfile.model_validate(profile_row.parsed),
        )

    async def update_profile(
        self, *, user_id: int, profile: ParsedProfile
    ) -> ProfileResponse:
        """Persist user edits and re-embed the corrected profile."""
        profile_row = await self._repository.get_active_profile(user_id)
        if profile_row is None:
            raise ProfileNotFoundError(user_id)

        summary_text = build_summary_text(profile)
        embedding_input_changed = summary_text != profile_row.summary_text
        matching_profile_changed = (
            build_matching_input_fingerprint(
                summary_text=summary_text, parsed_profile=profile.model_dump()
            )
            != build_matching_input_fingerprint(
                summary_text=profile_row.summary_text,
                parsed_profile=profile_row.parsed,
            )
        )
        if embedding_input_changed or profile_row.embedding is None:
            embedding = await embed_text(summary_text)
        else:
            embedding = profile_row.embedding
        await self._repository.update_profile_parsed(
            profile=profile_row,
            parsed=profile.model_dump(),
            summary_text=summary_text,
            embedding=embedding,
        )
        logger.info(
            "profile_updated",
            user_id=user_id,
            profile_id=profile_row.id,
            matching_profile_changed=matching_profile_changed,
        )
        return ProfileResponse(
            profile_id=profile_row.id,
            resume_id=profile_row.resume_id,
            profile=profile,
            matching_profile_changed=matching_profile_changed,
        )

    async def list_import_drafts(self, user_id: int) -> list[ResumeImportListItem]:
        drafts = await self._repository.list_import_drafts(user_id)
        return [
            ResumeImportListItem(
                resume_id=resume.id,
                filename=resume.filename,
                created_at=resume.created_at,
                lifecycle_status=ResumeLifecycleStatus(resume.lifecycle_status),
            )
            for resume, _profile in drafts
        ]

    async def get_import_draft(
        self, *, user_id: int, resume_id: int
    ) -> ResumeImportDetail:
        draft = await self._repository.get_import_draft(
            user_id=user_id, resume_id=resume_id
        )
        if draft is None:
            raise ResumeImportDraftNotFoundError(resume_id)
        resume, profile = draft
        return ResumeImportDetail(
            resume_id=resume.id,
            filename=resume.filename,
            created_at=resume.created_at,
            is_active=resume.is_active,
            lifecycle_status=ResumeLifecycleStatus(resume.lifecycle_status),
            profile=ParsedProfile.model_validate(profile.parsed),
            extraction_evidence=ExtractionEvidence.model_validate(
                getattr(profile, "extraction_evidence", None) or {}
            ),
            provenance=(
                ResumeImportProvenance.model_validate(resume.import_provenance)
                if getattr(resume, "import_provenance", None)
                else None
            ),
        )

    async def approve_import_draft(
        self, *, user_id: int, resume_id: int
    ) -> ProfileResponse:
        approval = await self._repository.approve_import_draft(
            user_id=user_id, resume_id=resume_id
        )
        if approval is None:
            raise ResumeImportDraftNotFoundError(resume_id)
        if not approval.activated:
            matching_profile_changed = False
        elif approval.previous_parsed is None:
            matching_profile_changed = True
        else:
            current_fingerprint = build_matching_input_fingerprint(
                summary_text=approval.profile.summary_text,
                parsed_profile=approval.profile.parsed,
            )
            previous_fingerprint = build_matching_input_fingerprint(
                summary_text=approval.previous_summary_text,
                parsed_profile=approval.previous_parsed,
            )
            embeddings_aligned = (
                approval.previous_embedding is not None
                and approval.profile.embedding is not None
                and list(approval.profile.embedding) == approval.previous_embedding
            )
            matching_profile_changed = (
                current_fingerprint != previous_fingerprint or not embeddings_aligned
            )
        return ProfileResponse(
            profile_id=approval.profile.id,
            resume_id=approval.profile.resume_id,
            profile=ParsedProfile.model_validate(approval.profile.parsed),
            matching_profile_changed=matching_profile_changed,
        )

    async def reject_import_draft(self, *, user_id: int, resume_id: int) -> None:
        rejected = await self._repository.reject_import_draft(
            user_id=user_id, resume_id=resume_id
        )
        if not rejected:
            raise ResumeImportDraftNotFoundError(resume_id)

    # -- saved tailored drafts ---------------------------------------------

    @staticmethod
    def _draft_response(draft: Any) -> TailoredResumeDraftResponse:
        return TailoredResumeDraftResponse(
            id=draft.id,
            job_id=draft.job_id,
            source_profile_id=draft.source_profile_id,
            job_title=draft.job_title,
            job_company=draft.job_company,
            created_at=draft.created_at,
            updated_at=draft.updated_at,
            content=TailoredResume.model_validate(draft.content),
        )

    @staticmethod
    def _draft_list_item(draft: Any) -> TailoredResumeDraftListItem:
        return TailoredResumeDraftListItem(
            id=draft.id,
            job_id=draft.job_id,
            source_profile_id=draft.source_profile_id,
            job_title=draft.job_title,
            job_company=draft.job_company,
            created_at=draft.created_at,
            updated_at=draft.updated_at,
        )

    async def create_tailored_draft(
        self,
        *,
        user_id: int,
        payload: TailoredResumeDraftCreate,
        job_repository: JobRepository,
    ) -> TailoredResumeDraftResponse:
        job = await job_repository.get_visible_by_id(
            user_id=user_id, job_id=payload.job_id
        )
        if job is None:
            raise JobNotFoundError(payload.job_id)

        try:
            source_pair = await self._repository.get_active_tailoring_source(user_id)
            if not isinstance(source_pair, tuple) or len(source_pair) != 2:
                raise ValueError
            profile, source_row = source_pair
            if source_row.content.get("schema_version") != "source-resume-v4":
                raise ValueError
            source_payload = SourceResumeContentPayloadV4.model_validate(source_row.content)
            if (
                source_row.validation_status != "verified"
                or source_payload.layout_validation_status != "verified"
                or source_payload.document.layout is None
            ):
                raise ValueError
            source_document = materialize_source_document_v4(source_payload.document)
            if source_document is None:
                raise ValueError
            profile_model = ParsedProfile.model_validate(profile.parsed)
            _validate_source_backed_snapshot(
                payload.content,
                source_document=source_document,
                profile=profile_model,
                job_description=getattr(job, "description", None),
            )
            if (
                payload.content.tailoring_snapshot_version != "lossless-complete-v2"
                or payload.content.source_resume_id != source_row.resume_id
                or payload.content.source_resume_content_id != source_row.id
                or payload.content.source_schema_version != "source-resume-v4"
                or payload.content.source_revision_sha256 != source_revision_sha256(source_row.content)
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise TailoredResumeSnapshotInvalidError() from exc

        profile_model = ParsedProfile.model_validate(profile.parsed)
        classifications = classify_requirements(
            getattr(job, "description", None), profile_model
        )
        authoritative_content = payload.content.model_copy(
            update={
                "requirement_classifications": classify_resume_coverage(
                    classifications, payload.content
                )
            }
        )
        content = authoritative_content.model_dump(mode="json")
        # Preserve the current source-backed snapshot shape when there are no
        # recognized job requirements; newly generated responses with
        # classifications retain that field in their immutable snapshot.
        if (
            not classifications
            and "requirement_classifications"
            not in payload.content.model_fields_set
        ):
            content.pop("requirement_classifications", None)
        if payload.content.tailoring_snapshot_version is None:
            content.pop("tailoring_snapshot_version", None)

        draft = await self._repository.create_tailored_draft(
            user_id=user_id,
            job_id=payload.job_id,
            source_profile_id=profile.id,
            source_resume_content_id=source_row.id,
            job_title=job.title,
            job_company=job.company,
            content=content,
        )
        return self._draft_response(draft)

    async def preview_resume_coverage(
        self,
        *,
        user_id: int,
        resume: TailoredResume,
        job_repository: JobRepository,
    ) -> TailoredResume:
        """Return advisory analysis for the submitted reviewed snapshot."""
        profile = await self._repository.get_active_profile(user_id)
        if profile is None:
            raise ProfileNotFoundError(user_id)
        job = await job_repository.get_visible_by_id(
            user_id=user_id, job_id=resume.job_id
        )
        if job is None:
            raise JobNotFoundError(resume.job_id)
        classifications = classify_requirements(
            getattr(job, "description", None), ParsedProfile.model_validate(profile.parsed)
        )
        return resume.model_copy(
            update={
                "requirement_classifications": classify_resume_coverage(
                    classifications, resume
                )
            }
        )

    async def validate_resume_export(
        self,
        *,
        user_id: int,
        resume: TailoredResume,
        job_repository: JobRepository,
    ) -> TailoredResume:
        """Validate that a reviewed snapshot is still losslessly source-backed."""
        source_pair = await self._repository.get_active_tailoring_source(user_id)
        if not isinstance(source_pair, tuple) or len(source_pair) != 2:
            raise TailoredResumeSnapshotInvalidError()
        profile_row, source_row = source_pair
        try:
            payload = SourceResumeContentPayloadV4.model_validate(source_row.content)
            if source_row.validation_status != "verified" or payload.layout_validation_status != "verified":
                raise ValueError
            source_document = materialize_source_document_v4(payload.document)
            if source_document is None:
                raise ValueError
            profile = ParsedProfile.model_validate(profile_row.parsed)
            job = await job_repository.get_visible_by_id(user_id=user_id, job_id=resume.job_id)
            if job is None:
                raise ValueError
            _validate_source_backed_snapshot(
                resume,
                source_document=source_document,
                profile=profile,
                job_description=job.description,
            )
            if (
                resume.tailoring_snapshot_version != "lossless-complete-v2"
                or resume.source_resume_id != source_row.resume_id
                or resume.source_resume_content_id != source_row.id
                or resume.source_schema_version != "source-resume-v4"
                or resume.source_revision_sha256 != source_revision_sha256(source_row.content)
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise TailoredResumeSnapshotInvalidError() from exc
        return resume

    async def list_tailored_drafts(
        self, *, user_id: int
    ) -> list[TailoredResumeDraftListItem]:
        drafts = await self._repository.list_tailored_drafts(user_id=user_id)
        return [self._draft_list_item(draft) for draft in drafts]

    async def get_tailored_draft(
        self, *, user_id: int, draft_id: int
    ) -> TailoredResumeDraftResponse:
        draft = await self._repository.get_tailored_draft(
            user_id=user_id, draft_id=draft_id
        )
        if draft is None:
            raise TailoredResumeDraftNotFoundError(draft_id)
        return self._draft_response(draft)

    async def delete_tailored_draft(
        self, *, user_id: int, draft_id: int
    ) -> None:
        deleted = await self._repository.delete_tailored_draft(
            user_id=user_id, draft_id=draft_id
        )
        if not deleted:
            raise TailoredResumeDraftNotFoundError(draft_id)

    # -- resume tailoring --------------------------------------------------

    async def tailor_for_job(
        self,
        *,
        user_id: int,
        job_id: int,
        job_repository: JobRepository,
    ) -> TailoredResume:
        """Selectively tailor source bullets for a specific job."""
        source_document = None
        source_pair = (
            await self._repository.get_active_tailoring_source(user_id)
            if hasattr(self._repository, "get_active_tailoring_source")
            else None
        )
        if source_pair is None and hasattr(self._repository, "get_active_tailoring_source"):
            raise ProfileNotFoundError(user_id)
        if not isinstance(source_pair, tuple) or len(source_pair) != 2:
            profile_row = await self._repository.get_active_profile(user_id)
            source_row = None
            source_roles = None
        else:
            profile_row, source_row = source_pair
            if source_row.content.get("schema_version") != "source-resume-v4":
                raise ResumeSourceLayoutUnavailableError()
            source_payload_v4 = SourceResumeContentPayloadV4.model_validate(source_row.content)
            if source_payload_v4.layout_validation_status != "verified" or source_payload_v4.document.layout is None:
                raise ResumeSourceLayoutUnavailableError()
            source_document = materialize_source_document_v4(source_payload_v4.document)
            source_roles = source_document.work_history if source_document is not None else None
            if source_roles is None or not any(role.highlights for role in source_roles):
                raise ResumeSourceLayoutUnavailableError()
        source_resume_id = source_row.resume_id if source_row is not None else None
        source_resume_content_id = source_row.id if source_row is not None else None
        source_revision = source_revision_sha256(source_row.content) if source_row is not None else None
        if profile_row is None:
            raise ProfileNotFoundError(user_id)
        canonical_sections = _canonical_sections(source_document)
        profile = ParsedProfile.model_validate(profile_row.parsed)
        job = await job_repository.get_visible_by_id(user_id=user_id, job_id=job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        classifications = classify_requirements(job.description, profile)
        matched_skills = [
            item.requirement
            for item in classifications
            if item.status == "verified"
        ]
        missing_skills = [
            item.requirement
            for item in classifications
            if item.status == "missing"
        ]
        report = SkillMatchReport(
            matched_skills=matched_skills,
            missing_skills=missing_skills,
            recall=(
                len(matched_skills) / len(classifications)
                if classifications
                else 0.0
            ),
        )
        if not job.description:
            # Return untailored — nothing to match against.
            return _untailored_fallback(
                profile,
                job_id,
                report,
                classifications,
                source_roles=source_roles,
                canonical_sections=canonical_sections,
                source_document=source_document,
                source_resume_id=source_resume_id,
                source_resume_content_id=source_resume_content_id,
                source_revision=source_revision,
            )

        selection = (
            select_source_bullets(source_roles, job.description)
            if source_roles is not None
            else None
        )
        recommended_orders = (
            selection.recommended_ids if selection is not None else None
        )
        output_orders = (
            _source_output_orders(source_roles, recommended_orders)
            if source_roles is not None and recommended_orders is not None
            else None
        )
        bullet_orders, eligible_bullets, prompt_roles = _bullet_relevance(
            profile,
            job.description,
            source_roles,
            selection.rewrite_candidate_ids if selection is not None else None,
        )
        fallback_orders = output_orders or bullet_orders
        if not any(eligible_bullets.values()):
            return _untailored_fallback(
                profile,
                job_id,
                report,
                classifications,
                fallback_orders,
                recommended_orders,
                source_roles,
                canonical_sections,
                source_document,
                source_resume_id=source_resume_id,
                source_resume_content_id=source_resume_content_id,
                source_revision=source_revision,
            )

        prompt = TAILOR_RESUME_PROMPT.format(
            job_description=job.description,
            eligible_bullets_json=json.dumps(prompt_roles, default=str),
            matched_skills=json.dumps(report.matched_skills),
            missing_skills=json.dumps(report.missing_skills),
        )

        try:
            response, model = await llm_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                json_mode=True,
            )
            raw = strip_code_fences(response.choices[0].message.content or "{}")
            data = json.loads(raw)
        except Exception:
            logger.warning(
                "resume.tailor.llm_failed",
                user_id=user_id,
                job_id=job_id,
                exc_info=True,
            )
            # Graceful fallback: return untailored profile with skill report.
            return _untailored_fallback(
                profile,
                job_id,
                report,
                classifications,
                fallback_orders,
                recommended_orders,
                source_roles,
                canonical_sections,
                source_document,
                source_resume_id=source_resume_id,
                source_resume_content_id=source_resume_content_id,
                source_revision=source_revision,
            )

        try:
            evidence_relevance: dict[str, int] = {}
            for role_entries in prompt_roles:
                for entry in role_entries:
                    source_uuid = entry.get("source_uuid")
                    score_value = entry.get("relevance_score")
                    if (
                        isinstance(source_uuid, str)
                        and isinstance(score_value, int)
                    ):
                        evidence_relevance[source_uuid] = score_value
            sanitized = sanitize_tailoring_output(
                profile,
                data,
                report.missing_skills,
                eligible_bullets,
                bullet_orders,
                (
                    build_source_evidence_catalog(source_roles)
                    if source_roles is not None
                    else build_evidence_catalog(profile)
                ),
                evidence_relevance,
                source_roles,
                output_orders=output_orders,
            )
        except Exception:
            logger.warning(
                "resume.tailor.output_invalid",
                user_id=user_id,
                job_id=job_id,
                exc_info=True,
            )
            return _untailored_fallback(
                profile,
                job_id,
                report,
                classifications,
                fallback_orders,
                recommended_orders,
                source_roles,
                canonical_sections,
                source_document,
                source_resume_id=source_resume_id,
                source_resume_content_id=source_resume_content_id,
                source_revision=source_revision,
            )

        matched = list(report.matched_skills)
        matched_set = set(matched)
        other = [s for s in profile.skills if normalize_skill(s) not in matched_set]
        missing = list(report.missing_skills)
        output_roles: list[Any] = (
            source_roles if source_roles is not None else profile.work_history
        )
        rendered_orders = output_orders or bullet_orders
        recommendation_orders = (
            recommended_orders
            if recommended_orders is not None
            else [[] for _ in output_roles]
        )
        tailored_wh = [
            TailoredWorkItem(
                company=(
                    source.company.text
                    if source_roles is not None and source.company
                    else source.company
                ),
                title=(
                    source.title.text
                    if source_roles is not None and source.title
                    else source.title
                ),
                start=(
                    source.start.text
                    if source_roles is not None and source.start
                    else source.start
                ),
                end=(
                    source.end.text
                    if source_roles is not None and source.end
                    else source.end
                ),
                original_highlights=(
                    [item.text for item in source.highlights]
                    if source_roles is not None
                    else list(source.highlights)
                ),
                tailored_highlights=sanitized_bullets,
                tailored_highlight_source_indices=[
                    next(
                        index
                        for index, bullet in enumerate(source.highlights)
                        if str(bullet.source_id) == source_uuid
                    )
                    if source_roles is not None
                    else int(source_uuid.rsplit(":", 1)[-1])
                    for source_uuid in rendered_orders[role_index]
                ],
                source_role_id=source.source_id if source_roles is not None else None,
                source_header=(
                    source.company.text
                    if source_roles is not None and source.company
                    else None
                ),
                role_context=(
                    [item.text for item in source.context_items]
                    if source_roles is not None
                    else []
                ),
                responsibilities_heading=(
                    source.responsibilities_heading.text
                    if source_roles is not None and source.responsibilities_heading
                    else None
                ),
                selection_decisions=(
                    _selection_decisions(
                        source,
                        recommendation_orders[role_index],
                        rendered_orders[role_index],
                        sanitized_bullets,
                    )
                    if source_roles is not None
                    else []
                ),
            )
            for role_index, (source, sanitized_bullets) in enumerate(
                zip(output_roles, sanitized.tailored_highlights, strict=True)
            )
        ]

        logger.info(
            "resume.tailored",
            user_id=user_id,
            job_id=job_id,
            model=model,
            roles=len(tailored_wh),
            summary_fallback=sanitized.summary_fallback,
            role_fallbacks=sanitized.role_fallbacks,
            bullet_fallbacks=sanitized.bullet_fallbacks,
            rejected_patches=sanitized.rejected_patches,
            rejection_reasons=sanitized.rejection_reasons,
            rejection_reason_counts=", ".join(
                f"{key}={value}"
                for key, value in sorted(sanitized.rejection_reasons.items())
            ),
            multi_evidence_selections=sanitized.multi_evidence_selections,
        )
        source_backed = source_document is not None
        source_skills = (
            [item.text for item in source_document.skills]
            if source_document is not None
            else list(profile.skills)
        )
        source_summary = (
            _join_wrapped_source([source_document.summary, *source_document.summary_lines])
            if source_document is not None
            else None
        )
        resume = TailoredResume(
            job_id=job_id,
            professional_summary=(
                source_summary if source_backed else sanitized.professional_summary
            ),
            full_name=None if source_backed else profile.full_name,
            email=None if source_backed else profile.email,
            phone=None if source_backed else profile.phone,
            headline=None if source_backed else profile.headline,
            skills=source_skills,
            links=profile.links.model_copy(deep=True) if not source_backed else {},
            projects=(
                [item.model_copy(deep=True) for item in profile.projects]
                if not source_backed
                else []
            ),
            languages=list(profile.languages) if not source_backed else [],
            licenses=list(profile.licenses) if not source_backed else [],
            education=list(profile.education) if not source_backed else [],
            certifications=list(profile.certifications) if not source_backed else [],
            work_history=tailored_wh,
            matched_skills=matched,
            other_skills=other,
            missing_keywords=missing,
            requirement_classifications=classifications,
            tailoring_snapshot_version=("lossless-complete-v2" if source_backed else None),
            canonical_sections=canonical_sections,
            source_resume_id=(source_row.resume_id if source_backed and source_row is not None else None),
            source_resume_content_id=(source_row.id if source_backed and source_row is not None else None),
            source_schema_version=("source-resume-v4" if source_backed else None),
            source_revision_sha256=(source_revision_sha256(source_row.content) if source_backed and source_row is not None else None),
            source_section_order=(list(source_document.section_order) if source_backed and source_document is not None else []),
        )
        resume.requirement_classifications = classify_resume_coverage(
            resume.requirement_classifications, resume
        )
        return resume
