"""Unit tests for Phase 4 schemas (cover letters + applications)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.applications.schemas import ApplicationUpdate
from app.domain.cover_letters.schemas import CoverLetterUpdate


def test_application_update_tracks_explicit_null_vs_unset() -> None:
    # cover_letter_id explicitly set to null -> should be in fields_set.
    explicit = ApplicationUpdate.model_validate({"cover_letter_id": None})
    assert "cover_letter_id" in explicit.model_fields_set

    # Omitted -> not in fields_set, so the service won't overwrite it.
    omitted = ApplicationUpdate.model_validate({"status": "applied"})
    assert "cover_letter_id" not in omitted.model_fields_set
    assert "notes" not in omitted.model_fields_set
    assert omitted.status == "applied"


def test_application_update_rejects_invalid_status() -> None:
    with pytest.raises(ValidationError):
        ApplicationUpdate.model_validate({"status": "ghosted"})


def test_application_update_accepts_known_statuses() -> None:
    for status in ("saved", "applied", "interview", "offer", "rejected", "closed"):
        assert ApplicationUpdate.model_validate({"status": status}).status == status


def test_cover_letter_update_requires_non_empty_content() -> None:
    with pytest.raises(ValidationError):
        CoverLetterUpdate.model_validate({"content": ""})
    assert CoverLetterUpdate.model_validate({"content": "Dear team"}).content
