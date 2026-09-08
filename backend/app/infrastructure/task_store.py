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
# ruff: noqa: E501

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable
from typing import Any, cast

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

    async def initialize_refresh_parent(self, task_id: str, children: list[str]) -> None:
        """Atomically initialize an owner-scoped refresh parent child set."""
        script = """
        local raw=redis.call('get', KEYS[1]); if not raw then return 0 end
        local d=cjson.decode(raw); if d.status=='done' or d.status=='failed' or d.status=='incomplete' then return 0 end
        d.expected_children=cjson.decode(ARGV[1]); d.children={}; d.source_results=cjson.empty_array; d.completed_children=0
        if #(d.expected_children)==0 then d.status='done'; d.message='No subscribed sources to refresh' else d.status='running'; d.message='Refreshing job sources…' end
        redis.call('setex', KEYS[1], ARGV[2], cjson.encode(d)); return 1
        """
        await cast(Awaitable[Any], self._r.eval(script, 1, self._key(task_id), json.dumps(children), str(_TTL)))

    async def record_refresh_child(self, task_id: str, child_key: str, payload: dict[str, Any]) -> None:
        """Record one child once and monotonically derive parent completion."""
        script = """
        local raw=redis.call('get', KEYS[1]); if not raw then return 0 end
        local d=cjson.decode(raw); if d.status=='done' or d.status=='failed' or d.status=='incomplete' then return 0 end
        d.children=d.children or {}; if d.children[ARGV[1]] then return 0 end
        local p=cjson.decode(ARGV[2]); d.children[ARGV[1]]=p; d.completed_children=(d.completed_children or 0)+1
        d.source_results={}; for _,child_key in ipairs(d.expected_children or {}) do if d.children[child_key] then table.insert(d.source_results,d.children[child_key]) end end
        d.fetched=(d.fetched or 0)+(p.fetched or 0); d.inserted=(d.inserted or 0)+(p.inserted or 0); d.updated=(d.updated or 0)+(p.updated or 0); d.embedded=(d.embedded or 0)+(p.embedded or 0)
        local expected=#(d.expected_children or {}); if d.completed_children >= expected then
          local incomplete=false; for _,v in pairs(d.children) do if v.status~='success' and v.status~='empty' and v.status~='skipped_fresh' then incomplete=true end end
          d.status=incomplete and 'incomplete' or 'done'; d.message=incomplete and 'Refresh completed with incomplete sources' or 'Refresh completed'
        else d.status='running'; d.message='Refreshing job sources…' end
        redis.call('setex', KEYS[1], ARGV[3], cjson.encode(d)); return 1
        """
        await cast(Awaitable[Any], self._r.eval(script, 1, self._key(task_id), child_key, json.dumps(payload), str(_TTL)))

    async def get(self, task_id: str) -> dict[str, Any] | None:
        raw = await self._r.get(self._key(task_id))
        if not raw:
            return None
        data = cast(dict[str, Any], json.loads(raw))
        # Redis Lua cjson cannot round-trip an empty array field reliably; the
        # polling contract nevertheless always exposes source_results as a list.
        if "source_results" not in data:
            data["source_results"] = []
        return data
