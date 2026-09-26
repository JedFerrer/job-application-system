"""Integration coverage for the expired-job retention migration path."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

pytestmark = pytest.mark.integration

_PRE_PURGE_REVISION = "0023_user_job_match_origin"


def _alembic_config() -> Config:
    backend_dir = Path(__file__).resolve().parents[2]
    return Config(str(backend_dir / "alembic.ini"))


@pytest.mark.migration
def test_migration_backfills_existing_expired_rows_once() -> None:
    """Upgrade 0023 -> 0024 gives old expired rows a fresh 90-day clock."""
    config = _alembic_config()
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    before_upgrade = datetime.now(UTC)

    try:
        command.downgrade(config, _PRE_PURGE_REVISION)
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE jobs, users RESTART IDENTITY CASCADE"))
            connection.execute(
                text(
                    "INSERT INTO jobs "
                    "(fingerprint, title, source, source_url, raw_response, status, "
                    "visibility, ingest_source) "
                    "VALUES "
                    "('migration0000001', 'Expired job', 'test', 'https://example.test/expired', "
                    "'{}'::jsonb, 'expired', 'public', 'scraper'), "
                    "('migration0000002', 'Active job', 'test', 'https://example.test/active', "
                    "'{}'::jsonb, 'active', 'public', 'scraper')"
                )
            )

        command.upgrade(config, "0024_expired_job_purge")

        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT status, expired_at FROM jobs ORDER BY fingerprint")
            ).all()

        assert rows[0].status == "expired"
        assert rows[0].expired_at is not None
        assert rows[0].expired_at >= before_upgrade
        assert rows[1].status == "active"
        assert rows[1].expired_at is None
    finally:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE jobs, users RESTART IDENTITY CASCADE"))
        engine.dispose()
