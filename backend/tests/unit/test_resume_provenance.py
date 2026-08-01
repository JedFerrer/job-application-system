from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.domain.resume import provenance
from app.domain.resume.completeness import COMPLETENESS_VALIDATOR_VERSION
from app.domain.resume.prompts import RESUME_EXTRACTION_PROMPT_VERSION
from app.domain.resume.provenance import (
    PROVENANCE_CONTRACT_VERSION,
    ResumeImportProvenance,
    build_resume_import_provenance,
)
from app.domain.resume.schemas import (
    PARSED_PROFILE_SCHEMA_VERSION,
    ExtractionWarning,
    ParsedProfile,
    ProfileResponse,
    ResumeImportListItem,
)
from app.domain.resume.service import ResumeService
from app.infrastructure.resume_parser import (
    EVIDENCE_VERIFIER_VERSION,
    NATIVE_EXTRACTION_VERSION,
    RESUME_PARSER_CONFIG_ID,
    ExtractedResumeText,
)

pytestmark = pytest.mark.unit


def test_provenance_contract_is_versioned_and_strict() -> None:
    provenance = build_resume_import_provenance(
        "resume.PDF", ["short_extracted_text", "likely_scanned_pdf"]
    )

    assert provenance.contract_version == PROVENANCE_CONTRACT_VERSION
    assert provenance.source_mime_type == "application/pdf"
    assert provenance.extraction_warning_codes == [
        "short_extracted_text",
        "likely_scanned_pdf",
    ]
    with pytest.raises(ValidationError):
        ResumeImportProvenance.model_validate(
            provenance.model_dump() | {"provider": "openai"}
        )


def test_builder_uses_authoritative_implementation_versions() -> None:
    provenance_value = build_resume_import_provenance("resume.pdf", [])

    assert provenance_value.parser_config_id == RESUME_PARSER_CONFIG_ID
    assert provenance_value.prompt_version == RESUME_EXTRACTION_PROMPT_VERSION
    assert provenance_value.parsed_profile_schema_version == PARSED_PROFILE_SCHEMA_VERSION
    assert provenance_value.evidence_verifier_version == EVIDENCE_VERIFIER_VERSION
    assert provenance_value.completeness_validator_version == COMPLETENESS_VALIDATOR_VERSION
    assert provenance_value.extraction_version == NATIVE_EXTRACTION_VERSION
    source = Path(provenance.__file__).read_text(encoding="utf-8")
    assert "resume-parser-v1" not in source
    assert "resume-extraction-prompt-v1" not in source
    assert "parsed-profile-v1" not in source
    assert "evidence-verifier-v1" not in source
    assert "completeness-validator-v1" not in source
    assert "native-extraction-v1" not in source


def test_provenance_rejects_completeness_warning_codes() -> None:
    payload = build_resume_import_provenance("resume.pdf", []).model_dump()
    payload["extraction_warning_codes"] = ["missing_work_history"]

    with pytest.raises(ValidationError):
        ResumeImportProvenance.model_validate(payload)


@pytest.mark.parametrize(
    ("filename", "mime_type"),
    [
        ("resume.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("resume.PDF", "application/pdf"),
    ],
)
def test_provenance_maps_accepted_source_types(
    filename: str, mime_type: str
) -> None:
    assert build_resume_import_provenance(filename, []).source_mime_type == mime_type


def test_provenance_rejects_unknown_source_type() -> None:
    with pytest.raises(ValueError, match="unsupported provenance source extension"):
        build_resume_import_provenance("resume.txt", [])


class _Repository:
    def __init__(self, previous: object | None = None) -> None:
        self.previous = previous
        self.added_resume: dict[str, object] | None = None

    async def get_active_profile(self, user_id: int) -> object | None:
        del user_id
        return self.previous

    async def lock_user_for_import(self, user_id: int) -> None:
        del user_id

    async def add_resume(self, **kwargs: object) -> object:
        self.added_resume = kwargs
        return SimpleNamespace(
            id=4,
            filename=kwargs["filename"],
            is_active=kwargs["is_active"],
            lifecycle_status=getattr(
                kwargs["lifecycle_status"], "value", kwargs["lifecycle_status"]
            ),
            import_provenance=kwargs["import_provenance"],
        )

    async def add_profile(self, **kwargs: object) -> object:
        return SimpleNamespace(**kwargs)


@pytest.mark.asyncio
async def test_service_persists_final_warning_codes_and_returns_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = ParsedProfile(headline="Engineer")
    repository = _Repository()
    monkeypatch.setattr(
        "app.domain.resume.service.extract_text_from_bytes",
        lambda *_args: ExtractedResumeText(
            "source text",
            [ExtractionWarning(code="short_extracted_text", message="review")],
        ),
    )
    async def parse(_: str) -> ParsedProfile:
        return profile

    async def embed(_: str) -> list[float]:
        return [0.1, 0.2]

    monkeypatch.setattr("app.domain.resume.service.parse_resume_text", parse)
    monkeypatch.setattr("app.domain.resume.service.embed_text", embed)

    result = await ResumeService(repository).upload_and_parse(  # type: ignore[arg-type]
        user_id=1, filename="resume.docx", content=b"source"
    )

    assert repository.added_resume is not None
    stored = repository.added_resume["import_provenance"]
    assert isinstance(stored, dict)
    assert stored["source_mime_type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert stored["extraction_warning_codes"] == ["short_extracted_text"]
    assert result.provenance is not None
    assert result.provenance.model_dump(mode="json") == stored


@pytest.mark.asyncio
async def test_service_separates_extraction_and_completeness_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extraction_warning = ExtractionWarning(
        code="short_extracted_text", message="review"
    )
    monkeypatch.setattr(
        "app.domain.resume.service.extract_text_from_bytes",
        lambda *_args: ExtractedResumeText(
            "ada@example.com\n\nEXPERIENCE\nBackend Engineer", [extraction_warning]
        ),
    )

    async def parse(_: str) -> ParsedProfile:
        return ParsedProfile()

    async def embed(_: str) -> list[float]:
        return [0.1, 0.2]

    monkeypatch.setattr("app.domain.resume.service.parse_resume_text", parse)
    monkeypatch.setattr("app.domain.resume.service.embed_text", embed)
    repository = _Repository()
    result = await ResumeService(repository).upload_and_parse(  # type: ignore[arg-type]
        user_id=1, filename="resume.pdf", content=b"source"
    )

    assert [warning.code for warning in result.extraction_warnings] == [
        "short_extracted_text",
        "source_email_not_parsed",
        "missing_work_history",
    ]
    assert repository.added_resume is not None
    stored = repository.added_resume["import_provenance"]
    assert isinstance(stored, dict)
    assert stored["extraction_warning_codes"] == ["short_extracted_text"]

    result.extraction_warnings.append(
        ExtractionWarning(code="missing_education", message="later mutation")
    )
    assert stored["extraction_warning_codes"] == ["short_extracted_text"]


@pytest.mark.asyncio
async def test_completeness_only_warnings_store_an_empty_extraction_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.domain.resume.service.extract_text_from_bytes",
        lambda *_args: ExtractedResumeText("EXPERIENCE\nBackend Engineer", []),
    )

    async def parse(_: str) -> ParsedProfile:
        return ParsedProfile()

    async def embed(_: str) -> list[float]:
        return [0.1, 0.2]

    monkeypatch.setattr("app.domain.resume.service.parse_resume_text", parse)
    monkeypatch.setattr("app.domain.resume.service.embed_text", embed)
    repository = _Repository()
    result = await ResumeService(repository).upload_and_parse(  # type: ignore[arg-type]
        user_id=1, filename="resume.pdf", content=b"source"
    )

    assert [warning.code for warning in result.extraction_warnings] == [
        "missing_work_history"
    ]
    assert repository.added_resume is not None
    stored = repository.added_resume["import_provenance"]
    assert isinstance(stored, dict)
    assert stored["extraction_warning_codes"] == []


@pytest.mark.asyncio
async def test_legacy_import_detail_keeps_null_provenance() -> None:
    class Repository:
        async def get_import_draft(self, **_kwargs: object) -> tuple[object, object]:
            return (
                SimpleNamespace(
                    id=4,
                    filename="legacy.pdf",
                    created_at=datetime(2026, 1, 1),
                    is_active=False,
                    lifecycle_status="draft",
                    import_provenance=None,
                ),
                SimpleNamespace(
                    parsed={"full_name": "Ada"},
                    extraction_evidence=None,
                ),
            )

    detail = await ResumeService(Repository()).get_import_draft(  # type: ignore[arg-type]
        user_id=1, resume_id=4
    )

    assert detail.provenance is None


@pytest.mark.asyncio
async def test_import_detail_exposes_provenance_but_profile_and_list_do_not() -> None:
    provenance = build_resume_import_provenance("draft.pdf", [])

    class Repository:
        async def get_import_draft(self, **_kwargs: object) -> tuple[object, object]:
            return (
                SimpleNamespace(
                    id=4,
                    filename="draft.pdf",
                    created_at=datetime(2026, 1, 1),
                    is_active=False,
                    lifecycle_status="draft",
                    import_provenance=provenance.model_dump(mode="json"),
                ),
                SimpleNamespace(parsed={"full_name": "Ada"}, extraction_evidence=None),
            )

    detail = await ResumeService(Repository()).get_import_draft(  # type: ignore[arg-type]
        user_id=1, resume_id=4
    )

    assert detail.provenance == provenance
    assert "provenance" not in ProfileResponse(
        profile_id=1, resume_id=4, profile=ParsedProfile()
    ).model_dump()
    assert "provenance" not in ResumeImportListItem(
        resume_id=4,
        filename="draft.pdf",
        created_at=datetime(2026, 1, 1),
        lifecycle_status="draft",
    ).model_dump()
