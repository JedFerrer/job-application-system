"""Integration coverage for the resume-import provenance migration."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

pytestmark = pytest.mark.integration

_PRE_PROVENANCE_REVISION = "0028_resume_extraction_evidence"
_PROVENANCE_REVISION = "0029_resume_import_provenance"


def _alembic_config() -> Config:
    backend_dir = Path(__file__).resolve().parents[2]
    return Config(str(backend_dir / "alembic.ini"))


@pytest.mark.migration
@pytest.mark.migration_start(_PRE_PROVENANCE_REVISION)
def test_provenance_migration_is_nullable_non_backfilled_and_reversible() -> None:
    """0028 -> 0029 adds only a nullable JSONB column and can be downgraded."""
    config = _alembic_config()
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    marker = uuid4().hex
    email = f"migration-{marker}@example.test"
    filename = f"migration-{marker}.pdf"
    user_id: int | None = None

    try:
        command.downgrade(config, _PRE_PROVENANCE_REVISION)
        with engine.begin() as connection:
            user_id = connection.execute(
                text(
                    "INSERT INTO users (id, email, name) "
                    "SELECT COALESCE(MAX(id), 0) + 1, :email, :name FROM users "
                    "RETURNING id"
                ),
                {"email": email, "name": "Migration Test"},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO resumes "
                    "(user_id, filename, raw_text, is_active, lifecycle_status) "
                    "VALUES (:user_id, :filename, :raw_text, false, 'draft')"
                ),
                {
                    "user_id": user_id,
                    "filename": filename,
                    "raw_text": "historical resume",
                },
            )

        command.upgrade(config, _PROVENANCE_REVISION)
        with engine.connect() as connection:
            column = connection.execute(
                text(
                    "SELECT data_type, is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_name = 'resumes' AND column_name = 'import_provenance'"
                )
            ).one()
            assert column == ("jsonb", "YES", None)

            indexes = connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename = 'resumes' AND indexdef ILIKE '%import_provenance%'"
                )
            ).all()
            assert indexes == []

            assert (
                connection.execute(
                    text("SELECT import_provenance FROM resumes WHERE filename = :filename"),
                    {"filename": filename},
                ).scalar_one()
                is None
            )

        command.downgrade(config, _PRE_PROVENANCE_REVISION)
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'resumes' AND column_name = 'import_provenance'"
                    )
                ).first()
                is None
            )
    finally:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            if user_id is not None:
                connection.execute(
                    text("DELETE FROM resumes WHERE filename = :filename"),
                    {"filename": filename},
                )
                connection.execute(
                    text("DELETE FROM users WHERE id = :user_id"),
                    {"user_id": user_id},
                )
        engine.dispose()
