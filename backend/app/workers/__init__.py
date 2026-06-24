"""Celery workers package (Phase 2).

Background and scheduled tasks live here per the backend instructions.
Importing :data:`celery_app` registers the task modules.
"""

from __future__ import annotations

from app.workers.celery_app import celery_app

__all__ = ["celery_app"]
