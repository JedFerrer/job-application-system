"""Isolated PostgreSQL coverage for the TFR-2 development-data reset."""

# SQL statements are intentionally kept readable and may exceed application
# line length; the database assertions remain explicit for reviewability.
# ruff: noqa: E501

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

pytestmark = pytest.mark.integration


def test_tfr2_reset_preserves_snapshots_and_unrelated_rows() -> None:
    backend_dir = Path(__file__).resolve().parents[2]
    config = Config(str(backend_dir / "alembic.ini"))
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    marker = uuid4().hex
    email = f"tfr2-migration-{marker}@example.test"
    filename = f"tfr2-migration-{marker}.pdf"
    user_id: int | None = None
    resume_id: int | None = None
    profile_id: int | None = None
    snapshot_id: int | None = None
    job_id: int | None = None

    try:
        command.downgrade(config, "0034_source_resume_contents")
        with engine.begin() as connection:
            user_id = connection.execute(
                text(
                    "INSERT INTO users (id, email, name) "
                    "SELECT COALESCE(MAX(id), 0) + 1, :email, 'TFR2 Test' FROM users "
                    "RETURNING id"
                ),
                {"email": email},
            ).scalar_one()
            job_id = connection.execute(
                text(
                    "INSERT INTO jobs (id, fingerprint, title, source, source_url, raw_response, "
                    "status, visibility, ingest_source) "
                    "SELECT COALESCE(MAX(id), 0) + 1, :fingerprint, 'TFR2 Job', 'test', :url, "
                    "'{}'::jsonb, 'active', 'public', 'test' FROM jobs "
                    "RETURNING id"
                ),
                    {"fingerprint": f"tfr2-{marker[:8]}", "url": f"https://example.test/{marker}"},
            ).scalar_one()
            resume_id = connection.execute(
                text(
                    "INSERT INTO resumes (user_id, filename, raw_text, is_active, lifecycle_status) "
                    "VALUES (:user_id, :filename, 'source', true, 'active') RETURNING id"
                ),
                {"user_id": user_id, "filename": filename},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO source_resume_contents "
                    "(resume_id, schema_version, content, validation_status, validation_errors) "
                    "VALUES (:resume_id, 'source-resume-v1', '{\"document\":{}}'::jsonb, 'verified', '[]'::jsonb)"
                ),
                {"resume_id": resume_id},
            )
            profile_id = connection.execute(
                text(
                    "INSERT INTO candidate_profiles "
                    "(user_id, resume_id, parsed, summary_text, embedding) "
                    "VALUES (:user_id, :resume_id, '{}'::jsonb, 'profile', "
                    "('[' || repeat('0,', 1535) || '0]')::vector) RETURNING id"
                ),
                {"user_id": user_id, "resume_id": resume_id},
            ).scalar_one()
            snapshot_id = connection.execute(
                text(
                    "INSERT INTO tailored_resume_drafts "
                    "(user_id, job_id, source_profile_id, job_title, content) "
                    "VALUES (:user_id, :job_id, :profile_id, 'TFR2 Job', "
                    "jsonb_build_object('keep', true)) RETURNING id"
                ),
                {"user_id": user_id, "job_id": job_id, "profile_id": profile_id},
            ).scalar_one()

        command.upgrade(config, "0035_clean_resume_invariant")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1 FROM resumes WHERE id = :id"), {"id": resume_id}).first() is None
            assert connection.execute(text("SELECT 1 FROM candidate_profiles WHERE id = :id"), {"id": profile_id}).first() is None
            assert connection.execute(text("SELECT 1 FROM source_resume_contents WHERE resume_id = :id"), {"id": resume_id}).first() is None
            snapshot = connection.execute(
                text("SELECT user_id, job_id, source_profile_id, content FROM tailored_resume_drafts WHERE id = :id"),
                {"id": snapshot_id},
            ).one()
            assert snapshot == (user_id, job_id, None, {"keep": True})
            assert connection.execute(text("SELECT 1 FROM users WHERE id = :id"), {"id": user_id}).first() is not None
            assert connection.execute(text("SELECT 1 FROM jobs WHERE id = :id"), {"id": job_id}).first() is not None

            new_resume = connection.execute(
                text(
                    "INSERT INTO resumes (user_id, filename, raw_text, is_active, lifecycle_status) "
                    "VALUES (:user_id, 'new.pdf', 'new', false, 'draft') RETURNING id"
                ),
                {"user_id": user_id},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO candidate_profiles (user_id, resume_id, parsed, summary_text, embedding) "
                    "VALUES (:user_id, :resume_id, '{}'::jsonb, 'new', "
                    "('[' || repeat('0,', 1535) || '0]')::vector)"
                ),
                {"user_id": user_id, "resume_id": new_resume},
            )
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO candidate_profiles (user_id, resume_id, parsed, summary_text, embedding) "
                        "VALUES (:user_id, :resume_id, '{}'::jsonb, 'duplicate', "
                        "('[' || repeat('0,', 1535) || '0]')::vector)"
                    ),
                    {"user_id": user_id, "resume_id": new_resume},
                )
    finally:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            if snapshot_id is not None:
                connection.execute(text("DELETE FROM tailored_resume_drafts WHERE id = :id"), {"id": snapshot_id})
            if user_id is not None:
                connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        engine.dispose()
