"""Database-free checks for narrow submission timeline response fields."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.applications.service import _timeline_item
from app.domain.applications.submissions import SubmissionService

pytestmark = pytest.mark.unit


def test_submission_timeline_exposes_only_retained_relation() -> None:
    item = _timeline_item(
        SimpleNamespace(  # type: ignore[arg-type]
            id="event-1",
            event_type="submission_confirmed",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            attempt_id=None,
            metadata_={
                "submission_id": 42,
                "submitted_at_explicit": True,
                "material_choice": "used_selected_package",
                "private": "do not expose",
            },
        )
    )
    assert item.submission_id == 42
    assert item.summary == "Submitted material explicitly confirmed"
    assert "private" not in item.model_dump_json()
    assert "submitted_at_explicit" not in item.model_dump_json()


def test_unrelated_timeline_event_has_no_submission_relation() -> None:
    item = _timeline_item(
        SimpleNamespace(  # type: ignore[arg-type]
            id="event-2",
            event_type="confirmation_detected",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            attempt_id=None,
            metadata_={"submission_id": 42},
        )
    )
    assert item.submission_id is None


def test_malformed_submission_relation_is_not_exposed() -> None:
    item = _timeline_item(
        SimpleNamespace(  # type: ignore[arg-type]
            id="event-3",
            event_type="submission_confirmed",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            attempt_id=None,
            metadata_={"submission_id": True},
        )
    )
    assert item.submission_id is None


@pytest.mark.asyncio
async def test_submission_time_provenance_uses_one_bounded_event_lookup() -> None:
    session = MagicMock(spec=AsyncSession)
    session.scalars = AsyncMock(
        return_value=SimpleNamespace(
            all=lambda: [
                {"submission_id": 4, "submitted_at_explicit": True},
                {"submission_id": 5, "submitted_at_explicit": False},
            ]
        )
    )
    service = SubmissionService(cast(AsyncSession, session))
    result = await service._reported_time_for_submissions(
        user_id=1,
        application_id=2,
        submission_ids=[4, 5],
    )
    assert result == {4: True, 5: False}
    session.scalars.assert_awaited_once()
    query = session.scalars.call_args.args[0]
    sql = str(
        query.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).lower()
    assert "application_events" in sql
    assert "application_artifacts" not in sql
    assert " in (4, 5)" in sql


@pytest.mark.asyncio
async def test_missing_submission_time_provenance_is_not_invented() -> None:
    session = MagicMock(spec=AsyncSession)
    session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: []))
    service = SubmissionService(cast(AsyncSession, session))
    with pytest.raises(RuntimeError, match="provenance is missing"):
        await service._reported_time_for_submissions(
            user_id=1,
            application_id=2,
            submission_ids=[4],
        )
