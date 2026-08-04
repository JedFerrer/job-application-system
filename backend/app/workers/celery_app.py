"""Celery application + Beat schedule (Phase 2).

The codebase is fully async; tasks bridge to the async services with
``asyncio.run(...)`` (see ``job_ingestion``/``maintenance``/``match_ranking``).
Cron expressions come from settings so schedules are configurable per env.

Run the worker + beat together:

    celery -A app.workers.celery_app worker --beat --loglevel=info
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

# Redis: db 0 for the broker, db 1 for results — mirrors the API's convention.
_broker_url = settings.redis_url
_backend_url = settings.redis_url.rsplit("/", 1)[0] + "/1"

celery_app = Celery(
    "job_application_system",
    broker=_broker_url,
    backend=_backend_url,
    include=[
        "app.workers.job_ingestion",
        "app.workers.job_imports",
        "app.workers.maintenance",
        "app.workers.match_ranking",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    task_acks_late=True,
    worker_max_tasks_per_child=200,
)


def _crontab(expression: str) -> crontab:
    """Parse a 5-field cron string into a Celery ``crontab`` schedule."""
    minute, hour, day_of_month, month_of_year, day_of_week = expression.split()
    return crontab(
        minute=minute,
        hour=hour,
        day_of_month=day_of_month,
        month_of_year=month_of_year,
        day_of_week=day_of_week,
    )


celery_app.conf.beat_schedule = {
    "dispatch-shared-scrape-queries": {
        "task": "dispatch_shared_scrape_queries",
        "schedule": _crontab(settings.scrape_schedule_cron),
    },
    "rank-matches-all-users": {
        "task": "run_match_ranking_for_all_users",
        "schedule": _crontab(settings.match_ranking_schedule_cron),
    },
    "archive-old-jobs": {
        "task": "archive_old_jobs",
        "schedule": _crontab(settings.archive_schedule_cron),
    },
    "purge-expired-jobs": {
        "task": "purge_expired_jobs",
        "schedule": _crontab(settings.purge_schedule_cron),
    },
    "purge-web-refresh-sessions": {
        "task": "purge_web_refresh_sessions",
        "schedule": _crontab(settings.web_refresh_session_cleanup_schedule_cron),
    },
}

celery_app.autodiscover_tasks(["app.workers"])
