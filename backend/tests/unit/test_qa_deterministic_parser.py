"""Guard tests for the explicitly isolated TFR-9 Browser QA parser."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.core.config import get_settings
from app.infrastructure import resume_parser

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_qa_parser_uses_fixture_only_with_all_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "qa_deterministic_resume.txt"
    extracted = fixture.read_text(encoding="utf-8")
    monkeypatch.setenv("QA_DETERMINISTIC_PARSER", "true")
    monkeypatch.setenv("QA_FIXTURE_SHA256", hashlib.sha256(extracted.encode()).hexdigest())
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/job_app_test"
    )
    get_settings.cache_clear()
    result = await resume_parser.parse_resume_text(extracted)
    assert result.source_validation_status == "verified"
    assert result.source_content is not None
    assert result.source_content.schema_version == "source-resume-v5"
    assert len(result.source_content.document.candidates) == 48
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_qa_parser_is_not_selected_for_production_or_other_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QA_DETERMINISTIC_PARSER", "true")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/job_app_test"
    )
    get_settings.cache_clear()
    llm = AsyncMock(side_effect=ValueError("normal parser selected"))
    monkeypatch.setattr(resume_parser, "llm_chat_completion", llm)
    with pytest.raises(ValueError, match="normal parser selected"):
        await resume_parser.parse_resume_text("An unrelated resume")
    llm.assert_awaited_once()
    get_settings.cache_clear()
