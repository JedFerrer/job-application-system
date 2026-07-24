"""Tests for the bounded job skill mention backfill command."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import app.scripts.backfill_job_skills as backfill_module
from app.scripts.backfill_job_skills import run_backfill

pytestmark = pytest.mark.unit


class FakeResult:
    def __init__(self, rows: list[tuple[int, str | None]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[int, str | None]]:
        return self._rows


class FakeSession:
    def __init__(
        self,
        jobs: list[tuple[int, str | None]],
        replacements: dict[int, tuple[object, ...]],
    ) -> None:
        self.jobs = jobs
        self.replacements = replacements

    async def execute(self, statement: object) -> FakeResult:
        compiled = str(statement)
        after_id = int(statement.compile().params["id_1"])
        limit = int(statement.compile().params["param_1"])
        rows = [(job_id, description) for job_id, description in self.jobs if job_id > after_id]
        if "ORDER BY" in compiled:
            rows.sort()
        return FakeResult(rows[:limit])

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> _AsyncContext:
        return _AsyncContext()


class _AsyncContext:
    async def __aenter__(self) -> _AsyncContext:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeFactory:
    def __init__(self, jobs: list[tuple[int, str | None]]) -> None:
        self.jobs = jobs
        self.sessions: list[FakeSession] = []
        self.replacements: dict[int, tuple[object, ...]] = {1: ("old",)}

    def __call__(self) -> FakeSession:
        session = FakeSession(self.jobs, self.replacements)
        self.sessions.append(session)
        return session


def _service_factory(session: FakeSession) -> SimpleNamespace:
    async def refresh_for_description(*, job_id: int, description: str | None) -> bool:
        if description and description.endswith("FAIL"):
            return False
        session.replacements[job_id] = ()
        return True

    return SimpleNamespace(refresh_for_description=refresh_for_description)


@pytest.mark.asyncio
async def test_backfill_honors_batch_size_after_id_and_limit() -> None:
    factory = FakeFactory([(1, "one"), (2, "two"), (3, "three"), (4, "four")])

    result = await run_backfill(
        batch_size=2,
        after_id=1,
        limit=2,
        session_factory=factory,  # type: ignore[arg-type]
        service_factory=_service_factory,  # type: ignore[arg-type]
    )

    assert result.processed == 2
    assert result.last_id == 3
    assert result.failed == 0


@pytest.mark.asyncio
async def test_backfill_is_repeatable_and_empty_descriptions_are_processed() -> None:
    factory = FakeFactory([(1, "Python"), (2, "")])
    service_factory = Mock(side_effect=_service_factory)

    first = await run_backfill(
        batch_size=2,
        session_factory=factory,  # type: ignore[arg-type]
        service_factory=service_factory,  # type: ignore[arg-type]
    )
    second = await run_backfill(
        batch_size=2,
        session_factory=factory,  # type: ignore[arg-type]
        service_factory=service_factory,  # type: ignore[arg-type]
    )

    assert (first.processed, first.replaced, first.empty) == (2, 1, 1)
    assert (second.processed, second.replaced, second.empty) == (2, 1, 1)


@pytest.mark.asyncio
async def test_backfill_continues_after_failure_and_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeFactory([(1, "PRIVATE DESCRIPTION FAIL"), (2, "safe")])
    warning = Mock()
    monkeypatch.setattr(backfill_module.logger, "warning", warning)

    result = await run_backfill(
        batch_size=2,
        session_factory=factory,  # type: ignore[arg-type]
        service_factory=_service_factory,  # type: ignore[arg-type]
    )

    assert result.processed == 2
    assert result.failed == 1
    assert result.replaced == 1
    assert factory.replacements[1] == ("old",)
    assert "PRIVATE DESCRIPTION FAIL" not in str(warning.call_args_list)
