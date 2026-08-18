"""Resume domain schemas.

``ParsedProfile`` is the structured contract the LLM must return when parsing
a resume, and is also the shape persisted in ``candidate_profiles.parsed``.
Ported/adapted from hr-ai-nterviewer-saas (``schemas/resume.py``): we keep the
``year`` int->str coercion and ``AliasChoices`` tolerance because LLMs drift on
field names and return numeric years.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, NamedTuple
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.domain.resume.lifecycle import ResumeLifecycleStatus
from app.domain.resume.provenance import ResumeImportProvenance

PARSED_PROFILE_SCHEMA_VERSION = "parsed-profile-v3"
SOURCE_RESUME_SCHEMA_VERSION = "source-resume-v1"
SOURCE_RESUME_SCHEMA_VERSION_V2 = "source-resume-v2"


def _normalize_optional_text(value: object) -> object:
    """Trim optional extracted text and normalize blanks to ``None``."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _normalize_list_text(value: object) -> object:
    """Trim non-empty strings in extracted list fields."""
    if value is None:
        return []
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return value


def _validate_profile_url(value: object) -> object:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return None
    if not isinstance(normalized, str):
        return normalized
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must use the http:// or https:// scheme")
    return normalized


class WorkItem(BaseModel):
    """A single work-history entry."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    company: str | None = None
    title: str | None = Field(
        default=None,
        validation_alias=AliasChoices("title", "role"),
    )
    start: str | None = None
    end: str | None = None
    highlights: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "highlights",
            "bullets",
            "achievements",
            "responsibilities",
            "details",
        ),
    )


class EducationItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    degree: str | None = None
    institution: str | None = None
    year: str | None = None

    @field_validator("year", mode="before")
    @classmethod
    def _coerce_year(cls, value: object) -> object:
        if value is None or isinstance(value, str):
            return value
        return str(value)


class ProfessionalLinks(BaseModel):
    """Optional professional links extracted from a resume."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    linkedin: str | None = Field(default=None, max_length=2048)
    github: str | None = Field(default=None, max_length=2048)
    portfolio: str | None = Field(default=None, max_length=2048)
    website: str | None = Field(default=None, max_length=2048)

    @field_validator("linkedin", "github", "portfolio", "website", mode="before")
    @classmethod
    def _normalize_url(cls, value: object) -> object:
        return _validate_profile_url(value)


class ProjectItem(BaseModel):
    """A project listed in the candidate's resume."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    technologies: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    url: str | None = Field(default=None, max_length=2048)

    @field_validator("name", "description", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> object:
        return _normalize_optional_text(value)

    @field_validator("technologies", "achievements", mode="before")
    @classmethod
    def _normalize_text_list(cls, value: object) -> object:
        return _normalize_list_text(value)

    @field_validator("url", mode="before")
    @classmethod
    def _normalize_url(cls, value: object) -> object:
        return _validate_profile_url(value)


class LanguageItem(BaseModel):
    """A language explicitly listed by the candidate."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    language: str = Field(min_length=1, max_length=100)
    proficiency: str | None = Field(default=None, max_length=100)

    @field_validator("language", mode="before")
    @classmethod
    def _normalize_language(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("proficiency", mode="before")
    @classmethod
    def _normalize_proficiency(cls, value: object) -> object:
        return _normalize_optional_text(value)


class ProfessionalCredential(BaseModel):
    """A professional license or registration explicitly listed by the candidate."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    issuer: str | None = Field(default=None, max_length=255)

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("issuer", mode="before")
    @classmethod
    def _normalize_issuer(cls, value: object) -> object:
        return _normalize_optional_text(value)


class ParsedProfile(BaseModel):
    """Normalized, validated resume payload (also the LLM output contract)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    full_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("full_name", "name"),
    )
    email: str | None = None
    phone: str | None = None
    headline: str | None = Field(
        default=None,
        validation_alias=AliasChoices("headline", "current_role", "summary"),
    )
    years_experience: float | None = None
    skills: list[str] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    languages: list[LanguageItem] = Field(default_factory=list, max_length=20)
    licenses: list[ProfessionalCredential] = Field(default_factory=list, max_length=30)
    links: ProfessionalLinks = Field(default_factory=ProfessionalLinks)
    projects: list[ProjectItem] = Field(default_factory=list)
    work_history: list[WorkItem] = Field(
        default_factory=list,
        validation_alias=AliasChoices("work_history", "experience"),
    )
    preferred_roles: list[str] = Field(default_factory=list)
    # entry | mid | senior | staff | lead
    seniority: str | None = None

    @field_validator("languages", mode="before")
    @classmethod
    def _deduplicate_languages(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        result: list[object] = []
        seen: set[str] = set()
        for item in value:
            if isinstance(item, dict):
                language = item.get("language")
            elif isinstance(item, LanguageItem):
                language = item.language
            else:
                language = None
            if isinstance(language, str):
                name = language.strip()
                key = name.casefold()
                if name and key in seen:
                    continue
                if name:
                    seen.add(key)
            result.append(item)
        return result

    @field_validator("licenses", mode="before")
    @classmethod
    def _deduplicate_licenses(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        result: list[object] = []
        seen: set[tuple[str, str]] = set()
        for item in value:
            if isinstance(item, dict):
                name, issuer = item.get("name"), item.get("issuer")
            elif isinstance(item, ProfessionalCredential):
                name, issuer = item.name, item.issuer
            else:
                name, issuer = None, None
            if isinstance(name, str):
                issuer_key = issuer.strip().casefold() if isinstance(issuer, str) else ""
                key = (name.strip().casefold(), issuer_key)
                if key[0] and key in seen:
                    continue
                if key[0]:
                    seen.add(key)
            result.append(item)
        return result

    @field_validator("years_experience", mode="before")
    @classmethod
    def _coerce_years(cls, value: object) -> object:
        if value is None or isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            match = re.search(r"\d+(?:\.\d+)?", value)
            return float(match.group()) if match else None
        return value


class EvidenceItem(BaseModel):
    """A short source excerpt associated with one parsed list item."""

    model_config = ConfigDict(extra="ignore")

    item_index: int = Field(ge=0)
    excerpt: str = Field(min_length=1, max_length=280)


class ExtractionEvidence(BaseModel):
    """Verified, optional source excerpts kept separate from profile facts."""

    model_config = ConfigDict(extra="ignore")

    work_history: list[EvidenceItem] = Field(default_factory=list)
    education: list[EvidenceItem] = Field(default_factory=list)
    certifications: list[EvidenceItem] = Field(default_factory=list)
    projects: list[EvidenceItem] = Field(default_factory=list)
    languages: list[EvidenceItem] = Field(default_factory=list)
    licenses: list[EvidenceItem] = Field(default_factory=list)


class ParsedResumeResult(NamedTuple):
    """Internal parser result; evidence is never part of ParsedProfile."""

    profile: ParsedProfile
    extraction_evidence: ExtractionEvidence
    source_content: SourceResumeContentPayload | SourceResumeContentPayloadV2 | None = None
    source_validation_status: Literal["verified", "invalid"] | None = None
    source_validation_errors: tuple[str, ...] = ()
    source_layout_status: Literal["verified", "invalid", "unavailable"] = "unavailable"
    source_layout_errors: tuple[str, ...] = ()


class SourceTextItem(BaseModel):
    """One literal source item with durable identity and raw-text proof."""

    model_config = ConfigDict(extra="ignore")

    source_id: UUID = Field(default_factory=uuid4)
    text: str = Field(min_length=1)
    order: int = Field(ge=0)
    fingerprint: str = ""
    start_offset: int = Field(default=0, ge=0)
    end_offset: int = Field(default=0, ge=0)
    occurrence_ordinal: int = Field(default=0, ge=0)

class SourceRole(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_id: UUID = Field(default_factory=uuid4)
    order: int = Field(default=0, ge=0)
    company: SourceTextItem | None = None
    title: SourceTextItem | None = None
    start: SourceTextItem | None = None
    end: SourceTextItem | None = None
    description: SourceTextItem | None = None
    highlights: list[SourceTextItem] = Field(default_factory=list)


class SourceProject(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_id: UUID = Field(default_factory=uuid4)
    order: int = Field(ge=0)
    name: SourceTextItem | None = None
    description: SourceTextItem | None = None
    technologies: list[SourceTextItem] = Field(default_factory=list)
    achievements: list[SourceTextItem] = Field(default_factory=list)
    url: SourceTextItem | None = None


class SourceResumeDocument(BaseModel):
    """Ordered source content; text items are always verified before storage."""

    model_config = ConfigDict(extra="ignore")

    contact: dict[str, SourceTextItem] = Field(default_factory=dict)
    links: dict[str, SourceTextItem] = Field(default_factory=dict)
    summary: SourceTextItem | None = None
    skills: list[SourceTextItem] = Field(default_factory=list)
    sections: list[SourceTextItem] = Field(default_factory=list)
    work_history: list[SourceRole] = Field(default_factory=list)
    projects: list[SourceProject] = Field(default_factory=list)
    education: list[SourceTextItem] = Field(default_factory=list)
    certifications: list[SourceTextItem] = Field(default_factory=list)
    licenses: list[SourceTextItem] = Field(default_factory=list)
    languages: list[SourceTextItem] = Field(default_factory=list)


class SourceResumeContentPayload(BaseModel):
    schema_version: Literal["source-resume-v1"] = "source-resume-v1"
    document: SourceResumeDocument


class SourceResumeCandidate(BaseModel):
    """Server-owned literal line candidate used by source-resume v2."""

    model_config = ConfigDict(extra="forbid")

    source_id: UUID = Field(default_factory=uuid4)
    text: str = Field(min_length=1)
    order: int = Field(ge=0)
    fingerprint: str = Field(min_length=1)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    occurrence_ordinal: int = Field(ge=0)


class SourceResumeRoleReferences(BaseModel):
    """Semantic references for one role; identity is one raw header candidate."""

    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=0)
    header: UUID
    highlights: list[UUID] = Field(default_factory=list)


class SourceResumeProjectReferences(BaseModel):
    """Semantic references for one project; text is resolved from the ledger."""

    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=0)
    name: UUID | None = None
    description: UUID | None = None
    technologies: list[UUID] = Field(default_factory=list)
    achievements: list[UUID] = Field(default_factory=list)
    url: UUID | None = None


class SourceResumeLayoutReferences(BaseModel):
    """Model-selected semantic layout, containing only candidate references."""

    model_config = ConfigDict(extra="forbid")

    contact: dict[str, UUID] = Field(default_factory=dict)
    links: dict[str, UUID] = Field(default_factory=dict)
    summary: UUID | None = None
    skills: list[UUID] = Field(default_factory=list)
    sections: list[UUID] = Field(default_factory=list)
    work_history: list[SourceResumeRoleReferences] = Field(default_factory=list)
    projects: list[SourceResumeProjectReferences] = Field(default_factory=list)
    education: list[UUID] = Field(default_factory=list)
    certifications: list[UUID] = Field(default_factory=list)
    licenses: list[UUID] = Field(default_factory=list)
    languages: list[UUID] = Field(default_factory=list)


class SourceResumeContentDocumentV2(BaseModel):
    """Verified candidate ledger plus optional semantic source layout."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[SourceResumeCandidate] = Field(default_factory=list)
    layout: SourceResumeLayoutReferences | None = None


class SourceResumeContentPayloadV2(BaseModel):
    """Canonical source-resume v2 payload persisted after import."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["source-resume-v2"] = "source-resume-v2"
    document: SourceResumeContentDocumentV2
    layout_validation_status: Literal["verified", "invalid", "unavailable"] = "unavailable"
    layout_validation_errors: list[str] = Field(default_factory=list)


class ExtractionWarning(BaseModel):
    """A deterministic, advisory warning about extracted resume text."""

    code: Literal[
        "short_extracted_text",
        "low_pdf_text_coverage",
        "likely_scanned_pdf",
        "source_email_not_parsed",
        "source_linkedin_not_parsed",
        "source_github_not_parsed",
        "missing_work_history",
        "missing_education",
        "missing_certifications",
        "missing_languages",
        "missing_licenses",
    ]
    message: str


class ResumeResponse(BaseModel):
    """API response after a resume is uploaded and parsed."""

    model_config = ConfigDict(from_attributes=True)

    resume_id: int
    filename: str
    is_active: bool
    lifecycle_status: ResumeLifecycleStatus = ResumeLifecycleStatus.ACTIVE
    profile: ParsedProfile
    matching_profile_changed: bool = True
    extraction_warnings: list[ExtractionWarning] = Field(default_factory=list)
    provenance: ResumeImportProvenance | None = None


class ResumeImportListItem(BaseModel):
    """Metadata for a pending imported-resume draft."""

    resume_id: int
    filename: str
    created_at: datetime
    lifecycle_status: ResumeLifecycleStatus


class ResumeImportDetail(BaseModel):
    """A pending imported resume with its parsed profile for review."""

    resume_id: int
    filename: str
    created_at: datetime
    is_active: bool
    lifecycle_status: ResumeLifecycleStatus
    profile: ParsedProfile
    extraction_evidence: ExtractionEvidence = Field(default_factory=ExtractionEvidence)
    provenance: ResumeImportProvenance | None = None


class ProfileResponse(BaseModel):
    """API response for the active candidate profile."""

    model_config = ConfigDict(from_attributes=True)

    profile_id: int
    resume_id: int
    profile: ParsedProfile
    matching_profile_changed: bool = False


class ProfileUpdate(BaseModel):
    """Editable profile fields (user can correct LLM mistakes)."""

    model_config = ConfigDict(extra="forbid")

    profile: ParsedProfile


# ---- Resume tailoring (Phase 2) ---------------------------------------------


class TailoredWorkItem(BaseModel):
    """A single work-history entry after tailoring for a specific job."""

    company: str | None = None
    title: str | None = None
    start: str | None = None
    end: str | None = None
    original_highlights: list[str] = Field(default_factory=list)
    tailored_highlights: list[str] = Field(default_factory=list)
    # Source identity is additive so legacy saved snapshots remain readable.
    # When present, this is a role-local permutation aligned with
    # ``tailored_highlights``.
    tailored_highlight_source_indices: list[int] = Field(default_factory=list)
    source_role_id: UUID | None = None
    # Canonical combined role header from source-resume-v2.  When present,
    # render this once rather than inferring company/title/date substrings.
    source_header: str | None = None
    selection_decisions: list[TailoredBulletDecision] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_source_mapping(self) -> TailoredWorkItem:
        if "tailored_highlight_source_indices" not in self.model_fields_set:
            return self
        mapping = self.tailored_highlight_source_indices
        if len(mapping) != len(self.tailored_highlights):
            raise ValueError(
                "tailored_highlight_source_indices must align with tailored_highlights"
            )
        if len(set(mapping)) != len(mapping):
            raise ValueError(
                "tailored_highlight_source_indices must map each selected source bullet once"
            )
        if not set(mapping).issubset(set(range(len(self.original_highlights)))):
            raise ValueError("tailored_highlight_source_indices contains an invalid source index")
        return self

    @model_validator(mode="after")
    def _validate_role_floor(self) -> TailoredWorkItem:
        if self.selection_decisions and not any(
            decision.final_included for decision in self.selection_decisions
        ):
            raise ValueError("each non-empty role must retain an included bullet")
        return self


class TailoredBulletDecision(BaseModel):
    """Canonical provenance and selection state for one work bullet."""

    source_id: UUID
    source_index: int = Field(ge=0)
    canonical_order: int = Field(ge=0)
    original_text: str
    suggested_text: str
    final_text: str
    recommended_included: bool
    final_included: bool
    recommendation_rank: int | None = Field(default=None, ge=0)
    output_rank: int | None = Field(default=None, ge=0)


class TailoredCanonicalItem(BaseModel):
    """One verified, literal non-work source item in a saved snapshot."""

    source_id: UUID
    text: str = Field(min_length=1)
    canonical_order: int = Field(ge=0)


class TailoredCanonicalSection(BaseModel):
    """Canonical pass-through content grouped by source section."""

    section: Literal[
        "contact",
        "links",
        "summary",
        "skills",
        "projects",
        "education",
        "certifications",
        "licenses",
        "languages",
    ]
    items: list[TailoredCanonicalItem] = Field(default_factory=list)


class RequirementClassification(BaseModel):
    """Ephemeral deterministic status for one recognized JD skill."""

    requirement: str
    status: Literal["verified", "missing"]
    priority: Literal["unclassified"] = "unclassified"
    evidence_categories: list[str] = Field(default_factory=list)
    resume_coverage_status: Literal[
        "covered", "not_represented", "not_applicable"
    ] | None = Field(default=None, exclude_if=lambda value: value is None)
    resume_evidence_categories: list[str] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )


class TailoredResume(BaseModel):
    """A profile rewritten to emphasize relevance for a specific job posting."""

    job_id: int
    professional_summary: str | None = None
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    headline: str | None = None
    # ``None`` preserves the distinction between legacy snapshots that did not
    # carry a skills field and current snapshots that explicitly carry ``[]``.
    skills: list[str] | None = None
    links: ProfessionalLinks = Field(default_factory=ProfessionalLinks)
    projects: list[ProjectItem] = Field(default_factory=list)
    languages: list[LanguageItem] = Field(default_factory=list)
    licenses: list[ProfessionalCredential] = Field(default_factory=list)
    work_history: list[TailoredWorkItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    matched_skills: list[str] = Field(default_factory=list)
    other_skills: list[str] = Field(default_factory=list)
    missing_keywords: list[str] = Field(default_factory=list)
    requirement_classifications: list[RequirementClassification] = Field(
        default_factory=list,
    )
    # New generated snapshots distinguish full-resume semantics from legacy
    # sparse snapshots.  ``None`` is intentionally preserved for history.
    tailoring_snapshot_version: Literal["full-resume-v1"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    # Present on new source-backed snapshots.  Absent on legacy snapshots.
    canonical_sections: list[TailoredCanonicalSection] = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
    )


class TailoredResumeDraftCreate(BaseModel):
    job_id: int
    content: TailoredResume

    @model_validator(mode="after")
    def _content_matches_job(self) -> TailoredResumeDraftCreate:
        if self.content.job_id != self.job_id:
            raise ValueError("content.job_id must match job_id")
        if self.content.skills is None:
            raise ValueError("content.skills must be an explicit list for new drafts")
        return self


class TailoredResumeDraftListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int | None
    source_profile_id: int | None
    job_title: str
    job_company: str | None
    created_at: datetime
    updated_at: datetime


class TailoredResumeDraftResponse(TailoredResumeDraftListItem):
    content: TailoredResume

    @field_serializer("content")
    def _serialize_content(self, content: TailoredResume) -> dict[str, object]:
        """Keep legacy draft JSON snapshots compact without changing the public model."""
        if not content.requirement_classifications:
            return content.model_dump(
                mode="json", exclude={"requirement_classifications"}
            )
        return content.model_dump(mode="json")
