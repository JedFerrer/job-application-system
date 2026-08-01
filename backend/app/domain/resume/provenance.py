"""Immutable provenance captured for one resume import attempt."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROVENANCE_CONTRACT_VERSION: Final[Literal["resume-import-provenance-v1"]] = (
    "resume-import-provenance-v1"
)

ExtractionWarningCode = Literal[
    "short_extracted_text",
    "low_pdf_text_coverage",
    "likely_scanned_pdf",
]

SourceMimeType = Literal[
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]


class ResumeImportProvenance(BaseModel):
    """Non-sensitive, immutable metadata for one successful import."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["resume-import-provenance-v1"] = PROVENANCE_CONTRACT_VERSION
    parser_config_id: str
    prompt_version: str
    parsed_profile_schema_version: str
    evidence_verifier_version: str
    completeness_validator_version: str
    extraction_method: str
    extraction_version: str
    source_mime_type: SourceMimeType
    extraction_warning_codes: list[ExtractionWarningCode] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_implementation_versions(self) -> ResumeImportProvenance:
        from app.domain.resume.completeness import COMPLETENESS_VALIDATOR_VERSION
        from app.domain.resume.prompts import RESUME_EXTRACTION_PROMPT_VERSION
        from app.domain.resume.schemas import PARSED_PROFILE_SCHEMA_VERSION
        from app.infrastructure.resume_parser import (
            EVIDENCE_VERIFIER_VERSION,
            NATIVE_EXTRACTION_VERSION,
            RESUME_PARSER_CONFIG_ID,
        )

        expected = {
            "parser_config_id": RESUME_PARSER_CONFIG_ID,
            "prompt_version": RESUME_EXTRACTION_PROMPT_VERSION,
            "parsed_profile_schema_version": PARSED_PROFILE_SCHEMA_VERSION,
            "evidence_verifier_version": EVIDENCE_VERIFIER_VERSION,
            "completeness_validator_version": COMPLETENESS_VALIDATOR_VERSION,
            "extraction_method": "native-pdf-docx",
            "extraction_version": NATIVE_EXTRACTION_VERSION,
        }
        for field, value in expected.items():
            if getattr(self, field) != value:
                raise ValueError(f"{field} does not match its implementation version")
        return self


_SOURCE_MIME_TYPES: dict[str, SourceMimeType] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def build_resume_import_provenance(
    filename: str, warning_codes: Sequence[str]
) -> ResumeImportProvenance:
    """Build provenance from already-validated import inputs."""
    from app.domain.resume.completeness import COMPLETENESS_VALIDATOR_VERSION
    from app.domain.resume.prompts import RESUME_EXTRACTION_PROMPT_VERSION
    from app.domain.resume.schemas import PARSED_PROFILE_SCHEMA_VERSION
    from app.infrastructure.resume_parser import (
        EVIDENCE_VERIFIER_VERSION,
        NATIVE_EXTRACTION_VERSION,
        RESUME_PARSER_CONFIG_ID,
    )

    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        source_mime_type = _SOURCE_MIME_TYPES[extension]
    except KeyError as exc:
        raise ValueError(f"unsupported provenance source extension {extension!r}") from exc
    return ResumeImportProvenance(
        parser_config_id=RESUME_PARSER_CONFIG_ID,
        prompt_version=RESUME_EXTRACTION_PROMPT_VERSION,
        parsed_profile_schema_version=PARSED_PROFILE_SCHEMA_VERSION,
        evidence_verifier_version=EVIDENCE_VERIFIER_VERSION,
        completeness_validator_version=COMPLETENESS_VALIDATOR_VERSION,
        extraction_method="native-pdf-docx",
        extraction_version=NATIVE_EXTRACTION_VERSION,
        source_mime_type=source_mime_type,
        extraction_warning_codes=list(warning_codes),
    )
