"""Unit tests for Phase 4 schemas (cover letters + applications)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.applications.schemas import (
    ApplicationListItem,
    ApplicationListResponse,
    ApplicationUpdate,
)
from app.domain.cover_letters.schemas import CoverLetterUpdate


def test_application_list_response_includes_limit_and_next_cursor() -> None:
    """ApplicationListResponse must include limit and next_cursor
    to match the cursor-pagination contract."""
    item = ApplicationListItem(
        id=1, job_id=10, job_title="Dev", company="Acme",
        location=None, source_url="https://example.com", status="saved",
        cover_letter_id=None, notes=None,
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )
    # With next_cursor
    resp = ApplicationListResponse(
        items=[item], total=1, limit=25, next_cursor="bmV4dA",
    )
    assert resp.limit == 25
    assert resp.next_cursor == "bmV4dA"
    assert resp.total == 1

    # Last page: next_cursor=None
    resp2 = ApplicationListResponse(
        items=[item], total=1, limit=25, next_cursor=None,
    )
    assert resp2.limit == 25
    assert resp2.next_cursor is None


# ── Existing tests ───────────────────────────────────────────────────────

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
