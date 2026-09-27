"""Read-only contract checks for the one-resume-per-job migration."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import Table

from app.models.resume import TailoredResumeDraft

pytestmark = pytest.mark.unit

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "0033_one_tailored_resume_per_job.py"
)


def test_model_declares_user_job_uniqueness() -> None:
    table = cast(Table, TailoredResumeDraft.__table__)
    constraints = {constraint.name for constraint in table.constraints if constraint.name}
    assert "uq_tailored_resume_drafts_user_job" in constraints


def test_migration_identity_and_deterministic_cleanup_contract() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "0033_one_tailored_resume_per_job"' in source
    assert 'down_revision: str | None = "0032_web_refresh_retention"' in source
    assert "WHERE job_id IS NOT NULL" in source
    assert "ORDER BY updated_at DESC, id DESC" in source
    assert "row_number > 1" in source
    assert '"uq_tailored_resume_drafts_user_job"' in source
    assert 'type_="unique"' in source
