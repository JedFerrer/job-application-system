"""Domain exceptions for the resume module.

Services raise these; route handlers / global handlers translate them to HTTP.
"""

from __future__ import annotations

from enum import StrEnum

type TailoredResumeDiagnosticValue = bool | int | str | None


class ResumeError(Exception):
    """Base class for resume domain errors."""


class UnsupportedResumeFormatError(ResumeError):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class ResumeParseError(ResumeError):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class ResumeTextTooLongError(ResumeError):
    """Raised when extracted resume text exceeds the application limit."""

    def __init__(self, actual_chars: int, max_chars: int) -> None:
        self.actual_chars = actual_chars
        self.max_chars = max_chars
        super().__init__(
            f"This resume contains {actual_chars} extracted characters, "
            f"exceeding the {max_chars:,}-character import limit. "
            "The resume was not imported."
        )


class ProfileNotFoundError(ResumeError):
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        super().__init__(f"No active profile for user {user_id}")


class ResumeImportDraftNotFoundError(ResumeError):
    """Raised when an owned resume is not a current import draft."""

    def __init__(self, resume_id: int) -> None:
        self.resume_id = resume_id
        super().__init__(f"Resume import draft {resume_id} not found")


class TailoredResumeDraftNotFoundError(ResumeError):
    def __init__(self, draft_id: int) -> None:
        self.draft_id = draft_id
        super().__init__(f"Tailored resume draft {draft_id} not found")


class TailoredResumeValidationReason(StrEnum):
    """Safe internal categories for rejected source-backed snapshots."""

    ACTIVE_SOURCE_UNAVAILABLE = "active_source_unavailable"
    SOURCE_SCHEMA_UNSUPPORTED = "source_schema_unsupported"
    SOURCE_STATE_INVALID = "source_state_invalid"
    SOURCE_DOCUMENT_UNAVAILABLE = "source_document_unavailable"
    SOURCE_PAYLOAD_INVALID = "source_payload_invalid"
    PROFILE_INVALID = "profile_invalid"
    REHYDRATION_ROLE_IDENTITY_MISMATCH = "rehydration_role_identity_mismatch"
    REHYDRATION_DECISION_MAPPING_MISMATCH = "rehydration_decision_mapping_mismatch"
    REHYDRATION_EVIDENCE_MISMATCH = "rehydration_evidence_mismatch"
    REHYDRATION_OUTPUT_ORDER_MISMATCH = "rehydration_output_order_mismatch"
    CANONICAL_SECTIONS_MISMATCH = "canonical_sections_mismatch"
    SUMMARY_EVIDENCE_MISMATCH = "summary_evidence_mismatch"
    SUMMARY_TEXT_MISMATCH = "summary_text_mismatch"
    CONTACT_FIELDS_MISMATCH = "contact_fields_mismatch"
    HEADLINE_OR_LINKS_MISMATCH = "headline_or_links_mismatch"
    SKILL_ORDER_MISMATCH = "skill_order_mismatch"
    SKILLS_MISMATCH = "skills_mismatch"
    NON_CANONICAL_SECTION_MISMATCH = "non_canonical_section_mismatch"
    ROLE_COUNT_MISMATCH = "role_count_mismatch"
    SNAPSHOT_VERSION_UNSUPPORTED = "snapshot_version_unsupported"
    REVIEW_EVIDENCE_VERSION_MISMATCH = "review_evidence_version_mismatch"
    REVIEW_EVIDENCE_MISMATCH = "review_evidence_mismatch"
    ROLE_IDENTITY_MISMATCH = "role_identity_mismatch"
    ROLE_SOURCE_FIELD_MISMATCH = "role_source_field_mismatch"
    ROLE_CONTEXT_MISMATCH = "role_context_mismatch"
    ORIGINAL_BULLETS_MISMATCH = "original_bullets_mismatch"
    DECISION_COUNT_MISMATCH = "decision_count_mismatch"
    DECISION_MAPPING_MISMATCH = "decision_mapping_mismatch"
    SUGGESTION_MISMATCH = "suggestion_mismatch"
    FINAL_TEXT_MISMATCH = "final_text_mismatch"
    EVIDENCE_MISMATCH = "evidence_mismatch"
    EVIDENCE_OWNERSHIP_MISMATCH = "evidence_ownership_mismatch"
    EVIDENCE_TEXT_MISMATCH = "evidence_text_mismatch"
    INCLUDED_OUTPUT_MISMATCH = "included_output_mismatch"
    OUTPUT_RANK_MISMATCH = "output_rank_mismatch"
    SOURCE_RESUME_ID_MISMATCH = "source_resume_id_mismatch"
    SOURCE_CONTENT_ID_MISMATCH = "source_content_id_mismatch"
    SOURCE_SCHEMA_MISMATCH = "source_schema_mismatch"
    SOURCE_REVISION_MISMATCH = "source_revision_mismatch"


class TailoredResumeSnapshotInvalidError(ResumeError):
    """A submitted fresh source-backed snapshot failed canonical validation."""

    def __init__(
        self,
        reason: TailoredResumeValidationReason = (
            TailoredResumeValidationReason.SOURCE_PAYLOAD_INVALID
        ),
        *,
        diagnostics: dict[str, TailoredResumeDiagnosticValue] | None = None,
    ) -> None:
        # Keep this deliberately non-specific: the response must not reveal
        # whether a submitted source identifier exists or belongs to anyone.
        self.reason = reason
        # Diagnostics are server-side structural metadata only. They are never
        # included in the public error response.
        self.diagnostics = diagnostics or {}
        super().__init__("The tailored resume is no longer valid; please regenerate it.")


class ResumeSourceLayoutUnavailableError(ResumeError):
    """The verified source ledger exists but semantic tailoring layout is unavailable."""

    def __init__(self) -> None:
        super().__init__(
            "Canonical source layout is unavailable; source-based tailoring cannot run."
        )
