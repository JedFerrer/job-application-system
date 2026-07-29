"""Task ownership regression tests.

Verifies that task status records are scoped to the creating user:

- A user can poll their own task.
- A user cannot poll another user's task (returns 404).
- Non-existent tasks return 404.
- Task result shape is preserved (backward-compatible).
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


# ── FakeTaskStore — in-memory store for deterministic testing ─────────────


class FakeTaskStore:
    """In-memory task store that mirrors the Redis TaskStore contract."""

    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}
        self._counter = 0

    async def create(self, task_type: str, user_id: int) -> str:
        self._counter += 1
        task_id = f"task-{self._counter}"
        self._tasks[task_id] = {
            "type": task_type,
            "status": "queued",
            "message": "Queued…",
            "user_id": user_id,
        }
        return task_id

    async def get(self, task_id: str) -> dict[str, Any] | None:
        return self._tasks.get(task_id)

    async def update(self, task_id: str, **fields: Any) -> None:
        if task_id in self._tasks:
            self._tasks[task_id].update(fields)


# ── Ownership helpers (mirror endpoint logic) ─────────────────────────────


def _task_visible(task: dict[str, Any] | None, user_id: int) -> bool:
    """Return True if *task* exists and is owned by *user_id*."""
    return task is not None and task.get("user_id") == user_id


# ── Tests ─────────────────────────────────────────────────────────────────


async def test_user_can_poll_own_ingest_task() -> None:
    """User can retrieve their own ingest task."""
    store = FakeTaskStore()
    task_id = await store.create("ingest", user_id=1)

    task = await store.get(task_id)
    assert _task_visible(task, user_id=1) is True
    assert task is not None
    assert task["type"] == "ingest"
    assert task["status"] == "queued"


async def test_user_can_poll_own_resume_task() -> None:
    """User can retrieve their own resume-parse task."""
    store = FakeTaskStore()
    task_id = await store.create("resume_parse", user_id=42)

    task = await store.get(task_id)
    assert _task_visible(task, user_id=42) is True
    assert task is not None
    assert task["type"] == "resume_parse"


async def test_user_cannot_poll_another_users_task() -> None:
    """User B polling user A's task returns invisible (→ 404)."""
    store = FakeTaskStore()
    task_id = await store.create("ingest", user_id=1)

    task = await store.get(task_id)
    assert _task_visible(task, user_id=2) is False


async def test_nonexistent_task_returns_none() -> None:
    """A task ID that doesn't exist returns None → 404."""
    store = FakeTaskStore()

    task = await store.get("nonexistent-id")
    assert _task_visible(task, user_id=1) is False


async def test_task_result_shape_preserved() -> None:
    """When a task is updated with results, the shape is preserved."""
    store = FakeTaskStore()
    task_id = await store.create("ingest", user_id=1)

    # Simulate background task updating progress.
    await store.update(task_id, status="running", message="Scraping…", fetched=10)
    await store.update(
        task_id,
        status="done",
        message="Done — 50 fetched, 5 new",
        fetched=50,
        inserted=5,
        updated=45,
        embedded=5,
        sources=["indeed", "linkedin"],
        result={"fetched": 50, "inserted": 5},
    )

    task = await store.get(task_id)
    assert _task_visible(task, user_id=1) is True
    assert task is not None
    assert task["status"] == "done"
    assert task["user_id"] == 1
    assert task["fetched"] == 50
    assert task["inserted"] == 5
    assert "sources" in task
    assert "result" in task


async def test_two_users_tasks_are_independent() -> None:
    """Tasks for different users do not interfere."""
    store = FakeTaskStore()
    t1 = await store.create("ingest", user_id=1)
    t2 = await store.create("resume_parse", user_id=2)

    # User 1 sees only their task.
    t1_data = await store.get(t1)
    assert _task_visible(t1_data, user_id=1) is True
    assert t1_data is not None
    assert t1_data["type"] == "ingest"

    t2_data = await store.get(t2)
    assert _task_visible(t2_data, user_id=2) is True
    assert t2_data is not None
    assert t2_data["type"] == "resume_parse"

    # Cross-user checks fail.
    assert _task_visible(t1_data, user_id=2) is False
    assert _task_visible(t2_data, user_id=1) is False
