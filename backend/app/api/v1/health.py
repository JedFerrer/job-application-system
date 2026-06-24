"""Health endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DbSession

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/detailed")
async def health_detailed(db: DbSession) -> dict[str, object]:
    try:
        await db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:  # noqa: BLE001 — report, don't crash the probe
        db_status = "unavailable"
    return {"status": "ok", "checks": {"database": db_status}}
