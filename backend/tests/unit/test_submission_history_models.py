"""Metadata parity for the 3C.1 persistence-only models."""

from __future__ import annotations

import pytest
from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from app.infrastructure.database import Base
from app.models import (
    ApplicationInterviewPrep,
    ApplicationSubmission,
    ApplicationSubmissionArtifact,
)

pytestmark = pytest.mark.unit


def test_submission_models_are_registered_without_changing_legacy_models() -> None:
    assert {
        "application_submissions",
        "application_submission_artifacts",
        "application_interview_preps",
    } <= set(Base.metadata.tables)
    assert ApplicationSubmission.__table__ is Base.metadata.tables["application_submissions"]
    assert (
        ApplicationSubmissionArtifact.__table__
        is Base.metadata.tables["application_submission_artifacts"]
    )
    assert ApplicationInterviewPrep.__table__ is Base.metadata.tables["application_interview_preps"]
    assert "interview_preps" in Base.metadata.tables
    assert "application_events" in Base.metadata.tables
    assert "status" in Base.metadata.tables["applications"].columns


def test_submission_and_interview_column_contract() -> None:
    submission = ApplicationSubmission.__table__.columns
    assert isinstance(submission["submitted_at"].type, DateTime)
    assert submission["submitted_at"].type.timezone
    assert submission["submitted_at"].server_default is None
    assert isinstance(submission["created_at"].type, DateTime)
    assert submission["created_at"].type.timezone
    assert isinstance(submission["note"].type, String)
    assert submission["note"].type.length == 10_000
    assert isinstance(submission["idempotency_key"].type, String)
    assert submission["idempotency_key"].type.length == 128

    prep = ApplicationInterviewPrep.__table__.columns
    assert isinstance(prep["focus_areas"].type, ARRAY)
    assert isinstance(prep["questions"].type, JSONB)
    assert isinstance(prep["evidence"].type, JSONB)
    assert isinstance(prep["created_at"].type, DateTime)
    assert prep["created_at"].type.timezone
    assert isinstance(prep["updated_at"].type, DateTime)
    assert prep["updated_at"].type.timezone
