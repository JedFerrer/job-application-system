"""Redis-backed task status store for long-running background operations.

Each task is a JSON object stored with a 1-hour TTL.  Common fields:

    type      "ingest" | "resume_parse"
    status    "queued" | "running" | "done" | "failed"
    message   Human-readable progress description
    error     Error message when status == "failed"
    result    Final JSON payload when status == "done"

Ingest-specific fields (updated incrementally):
    fetched   jobs fetched so far
    inserted  new jobs upserted
    updated   existing jobs refreshed
    embedded  jobs embedded
    sources   list of source names used
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import redis.asyncio as aioredis

_TTL = 3600  # 1 hour


class TaskStore:
    def __init__(self, redis: aioredis.Redis) -> None:
        self._r = redis

    @staticmethod
    def _key(task_id: str) -> str:
        return f"task:{task_id}"

    async def create(self, task_type: str, user_id: int) -> str:
        """Create a new task owned by *user_id*.

        Returns the generated ``task_id`` (UUID).
        """
        task_id = str(uuid.uuid4())
        await self._r.setex(
            self._key(task_id),
            _TTL,
            json.dumps({
                "type": task_type,
                "status": "queued",
                "message": "Queued…",
                "user_id": user_id,
            }),
        )
        return task_id

    async def update(self, task_id: str, **fields: Any) -> None:
        key = self._key(task_id)
        raw = await self._r.get(key)
        if raw:
            data = json.loads(raw)
            data.update(fields)
            await self._r.setex(key, _TTL, json.dumps(data))

    async def get(self, task_id: str) -> dict[str, Any] | None:
        raw = await self._r.get(self._key(task_id))
        return json.loads(raw) if raw else None
