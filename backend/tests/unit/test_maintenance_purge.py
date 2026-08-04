"""Unit tests for scheduled expired-job purge maintenance."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.domain.jobs.repository import ExpiredJobPurgeBatch
from app.workers import maintenance
from app.workers.celery_app import celery_app

pytestmark = pytest.mark.unit


class _SessionContext:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> _SessionContext:
        return self

    async def __aexit__(self, *args: object) -> bool:
        del args
        return False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


def _batch(
    *,
    scanned: int,
    protected: int,
    purged: int,
    next_cursor: tuple[datetime, int] | None = None,
) -> ExpiredJobPurgeBatch:
    return ExpiredJobPurgeBatch(
        scanned=scanned,
        protected=protected,
        purged=purged,
        next_cursor=next_cursor,
    )


@pytest.mark.asyncio
async def test_purge_uses_configured_retention_and_batch_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionContext()
    observed: dict[str, object] = {}

    class Repository:
        def __init__(self, db: object) -> None:
            assert db is session

        async def purge_expired_unreferenced(
            self, **kwargs: object
        ) -> ExpiredJobPurgeBatch:
            observed.update(kwargs)
            return _batch(scanned=4, protected=3, purged=1)

    monkeypatch.setattr(maintenance, "_worker_session_factory", lambda: session)
    monkeypatch.setattr(maintenance, "JobRepository", Repository)
    monkeypatch.setattr(
        maintenance,
        "get_settings",
        lambda: SimpleNamespace(job_purge_after_days=90, job_purge_batch_size=123),
    )

    result = await maintenance._purge_expired_jobs_async()

    assert result == {"scanned": 4, "protected": 3, "purged": 1}
    assert observed["batch_size"] == 123
    assert observed["after"] is None
    assert session.committed is True


@pytest.mark.asyncio
async def test_purge_processes_batches_in_separate_transactions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = [_SessionContext(), _SessionContext()]
    calls = 0
    cursor = (datetime.now(UTC), 2)

    class Repository:
        def __init__(self, db: object) -> None:
            assert db is sessions[calls]

        async def purge_expired_unreferenced(
            self, **kwargs: object
        ) -> ExpiredJobPurgeBatch:
            nonlocal calls
            if calls == 0:
                assert kwargs["after"] is None
                calls += 1
                return _batch(
                    scanned=2,
                    protected=1,
                    purged=1,
                    next_cursor=cursor,
                )
            assert kwargs["after"] == cursor
            calls += 1
            return _batch(scanned=1, protected=0, purged=1)

    session_iterator = iter(sessions)
    monkeypatch.setattr(maintenance, "_worker_session_factory", lambda: next(session_iterator))
    monkeypatch.setattr(maintenance, "JobRepository", Repository)
    monkeypatch.setattr(
        maintenance,
        "get_settings",
        lambda: SimpleNamespace(job_purge_after_days=90, job_purge_batch_size=2),
    )

    result = await maintenance._purge_expired_jobs_async()

    assert result == {"scanned": 3, "protected": 1, "purged": 2}
    assert calls == 2
    assert all(session.committed for session in sessions)


@pytest.mark.asyncio
async def test_purge_rolls_back_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _SessionContext()

    class Repository:
        def __init__(self, db: object) -> None:
            del db

        async def purge_expired_unreferenced(
            self, **kwargs: object
        ) -> ExpiredJobPurgeBatch:
            del kwargs
            raise RuntimeError("database failure")

    monkeypatch.setattr(maintenance, "_worker_session_factory", lambda: session)
    monkeypatch.setattr(maintenance, "JobRepository", Repository)
    monkeypatch.setattr(
        maintenance,
        "get_settings",
        lambda: SimpleNamespace(job_purge_after_days=90, job_purge_batch_size=500),
    )

    with pytest.raises(RuntimeError, match="database failure"):
        await maintenance._purge_expired_jobs_async()

    assert session.rolled_back is True


@pytest.mark.asyncio
async def test_web_session_cleanup_commits_each_batch_and_reports_cutoffs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = [_SessionContext(), _SessionContext()]
    observed: list[dict[str, object]] = []

    class Repository:
        def __init__(self, db: object) -> None:
            assert db is sessions[len(observed)]

        async def delete_unusable_sessions(self, **kwargs: object) -> int:
            observed.append(kwargs)
            return 2 if len(observed) == 1 else 1

    session_iterator = iter(sessions)
    monkeypatch.setattr(maintenance, "_worker_session_factory", lambda: next(session_iterator))
    monkeypatch.setattr(maintenance, "WebSessionRepository", Repository)
    monkeypatch.setattr(
        maintenance,
        "get_settings",
        lambda: SimpleNamespace(
            refresh_token_expire_days=7,
            web_refresh_session_retention_days=30,
            web_refresh_session_cleanup_batch_size=2,
        ),
    )

    result = await maintenance._purge_web_refresh_sessions_async()

    assert result == {"deleted": 3, "batches": 2}
    assert observed[0]["batch_size"] == 2
    assert observed[0]["revoked_before"] == observed[1]["revoked_before"]
    assert observed[0]["never_revoked_before"] == observed[1]["never_revoked_before"]
    assert all(session.committed for session in sessions)


def test_beat_schedule_registers_daily_web_session_cleanup() -> None:
    schedule = celery_app.conf.beat_schedule["purge-web-refresh-sessions"]

    assert schedule["task"] == "purge_web_refresh_sessions"
    assert schedule["schedule"].minute == {0}
    assert schedule["schedule"].hour == {4}
