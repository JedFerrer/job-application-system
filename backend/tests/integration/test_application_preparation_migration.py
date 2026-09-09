"""0038 -> 0039 application preparation/package migration regression coverage."""

# ruff: noqa: E501

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

pytestmark = pytest.mark.integration

_PREVIOUS = "0038_source_refresh_state"
_CURRENT = "0039_application_packages"
_ZERO_VECTOR = "[" + ",".join(["0"] * 1536) + "]"


def _config() -> Config:
    return Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))


def _revision(connection: object) -> str:
    return str(connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one())  # type: ignore[attr-defined]


def test_0039_upgrade_schema_fk_behavior_and_downgrade() -> None:
    """Exercise the actual PostgreSQL migration, including its reversible FK graph."""
    config = _config()
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    marker = uuid4().hex[:10]
    try:
        command.downgrade(config, _PREVIOUS)
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE users CASCADE"))
            user_id = connection.execute(
                text("INSERT INTO users (email, name) VALUES (:email, 'Migration') RETURNING id"),
                {"email": f"preparation-migration-{marker}@example.test"},
            ).scalar_one()
            job_id = connection.execute(
                text(
                    "INSERT INTO jobs (fingerprint, identity_scope, identity_key, title, company, "
                    "source, source_url, raw_response, status, visibility, ingest_source) "
                    "VALUES (:fingerprint, 'public', :identity, 'Engineer', 'Acme', 'fixture', "
                    "'https://example.test/jobs/migration', '{}'::jsonb, 'active', 'public', 'scraper') "
                    "RETURNING id"
                ),
                {"fingerprint": f"prep-{marker}", "identity": f"migration:{marker}"},
            ).scalar_one()
            application_id = connection.execute(
                text(
                    "INSERT INTO applications (user_id, job_id, status, notes) "
                    "VALUES (:user_id, :job_id, 'saved', 'Created under 0038') RETURNING id"
                ),
                {"user_id": user_id, "job_id": job_id},
            ).scalar_one()
        command.upgrade(config, _CURRENT)
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert {"application_preparations", "application_packages"} <= set(
                inspector.get_table_names()
            )
            assert _revision(connection) == _CURRENT

            package_unique = {
                tuple(constraint["column_names"])
                for constraint in inspector.get_unique_constraints("application_packages")
            }
            assert {
                ("application_id", "revision"),
                ("application_id", "idempotency_key"),
                ("application_id", "content_sha256"),
            } <= package_unique
            package_fks = {
                fk["referred_table"]: fk.get("options", {}).get("ondelete")
                for fk in inspector.get_foreign_keys("application_packages")
            }
            assert package_fks["applications"] == "CASCADE"
            assert package_fks["tailored_resume_drafts"] == "SET NULL"
            assert package_fks["cover_letters"] == "SET NULL"
            preparation_fks = {
                fk["referred_table"]: fk.get("options", {}).get("ondelete")
                for fk in inspector.get_foreign_keys("application_preparations")
            }
            assert preparation_fks["applications"] == "CASCADE"
            assert preparation_fks["application_packages"] == "SET NULL"
            assert preparation_fks["cover_letters"] == "SET NULL"
            checks = inspector.get_check_constraints("application_preparations")
            assert any("cover_letter_mode" in str(check.get("sqltext")) for check in checks)
            preserved = connection.execute(
                text(
                    "SELECT id, user_id, job_id, status, notes FROM applications WHERE id = :id"
                ),
                {"id": application_id},
            ).mappings().one()
            assert dict(preserved) == {
                "id": application_id,
                "user_id": user_id,
                "job_id": job_id,
                "status": "saved",
                "notes": "Created under 0038",
            }
            assert connection.execute(text("SELECT count(*) FROM application_preparations")).scalar_one() == 0
            assert connection.execute(text("SELECT count(*) FROM application_packages")).scalar_one() == 0

        with engine.begin() as connection:
            extra_application_ids = []
            for index, suffix in enumerate(("not-needed", "selected-null", "invalid"), start=1):
                extra_job_id = connection.execute(
                    text(
                        "INSERT INTO jobs (fingerprint, identity_scope, identity_key, title, company, "
                        "source, source_url, raw_response, status, visibility, ingest_source) "
                        "VALUES (:fingerprint, 'public', :identity, 'Engineer', 'Acme', 'fixture', "
                        ":url, '{}'::jsonb, 'active', 'public', 'scraper') RETURNING id"
                    ),
                    {
                        "fingerprint": f"p{marker}{index}",
                        "identity": f"migration:{marker}:{suffix}",
                        "url": f"https://example.test/jobs/migration/{suffix}",
                    },
                ).scalar_one()
                extra_application_ids.append(
                    connection.execute(
                        text(
                            "INSERT INTO applications (user_id, job_id, status) "
                            "VALUES (:user_id, :job_id, 'saved') RETURNING id"
                        ),
                        {"user_id": user_id, "job_id": extra_job_id},
                    ).scalar_one()
                )
            not_needed_application_id, selected_null_application_id, invalid_application_id = (
                extra_application_ids
            )
            letter_id = connection.execute(
                text(
                    "INSERT INTO cover_letters (user_id, job_id, content, model, is_edited) "
                    "VALUES (:user_id, :job_id, 'Letter', 'fixture', false) RETURNING id"
                ),
                {"user_id": user_id, "job_id": job_id},
            ).scalar_one()
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO application_preparations "
                            "(application_id, cover_letter_mode, cover_letter_id) "
                            "VALUES (:application_id, 'not_needed', :letter_id)"
                        ),
                        {"application_id": invalid_application_id, "letter_id": letter_id},
                    )
            connection.execute(
                text(
                    "INSERT INTO application_preparations (application_id, cover_letter_mode) "
                    "VALUES (:application_id, 'not_needed')"
                ),
                {"application_id": not_needed_application_id},
            )
            connection.execute(
                text(
                    "INSERT INTO application_preparations (application_id, cover_letter_mode) "
                    "VALUES (:application_id, 'selected')"
                ),
                {"application_id": selected_null_application_id},
            )
            package_id = connection.execute(
                text(
                    "INSERT INTO application_packages "
                    "(application_id, revision, content, content_sha256, schema_version, idempotency_key, cover_letter_id) "
                    "VALUES (:application_id, 1, '{}'::jsonb, :hash, 'application-package-v1', 'migration', :letter_id) "
                    "RETURNING id"
                ),
                {"application_id": application_id, "hash": "a" * 64, "letter_id": letter_id},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO application_preparations "
                    "(application_id, cover_letter_mode, cover_letter_id, approved_package_id) "
                    "VALUES (:application_id, 'selected', :letter_id, :package_id)"
                ),
                {"application_id": application_id, "letter_id": letter_id, "package_id": package_id},
            )
            connection.execute(text("DELETE FROM cover_letters WHERE id = :id"), {"id": letter_id})
            assert connection.execute(
                text("SELECT cover_letter_id FROM application_packages WHERE id = :id"),
                {"id": package_id},
            ).scalar_one() is None
            assert connection.execute(
                text("SELECT cover_letter_id FROM application_preparations WHERE application_id = :id"),
                {"id": application_id},
            ).scalar_one() is None
            assert connection.execute(
                text("SELECT cover_letter_mode FROM application_preparations WHERE application_id = :id"),
                {"id": application_id},
            ).scalar_one() == "selected"
            resume_id = connection.execute(
                text(
                    "INSERT INTO resumes (user_id, filename, raw_text, is_active, lifecycle_status) "
                    "VALUES (:user_id, 'source.pdf', 'Source', true, 'active') RETURNING id"
                ),
                {"user_id": user_id},
            ).scalar_one()
            source_id = connection.execute(
                text(
                    "INSERT INTO source_resume_contents "
                    "(resume_id, schema_version, content, validation_status, validation_errors) "
                    "VALUES (:resume_id, 'source-resume-v3', '{}'::jsonb, 'verified', '[]'::jsonb) "
                    "RETURNING id"
                ),
                {"resume_id": resume_id},
            ).scalar_one()
            profile_id = connection.execute(
                text(
                    "INSERT INTO candidate_profiles "
                    "(user_id, resume_id, parsed, summary_text, embedding) "
                    "VALUES (:user_id, :resume_id, '{}'::jsonb, 'Source', (:embedding)::vector) "
                    "RETURNING id"
                ),
                {"user_id": user_id, "resume_id": resume_id, "embedding": _ZERO_VECTOR},
            ).scalar_one()
            draft_id = connection.execute(
                text(
                    "INSERT INTO tailored_resume_drafts "
                    "(user_id, job_id, source_profile_id, source_resume_content_id, job_title, content) "
                    "VALUES (:user_id, :job_id, :profile_id, :source_id, 'Engineer', '{}'::jsonb) "
                    "RETURNING id"
                ),
                {
                    "user_id": user_id,
                    "job_id": job_id,
                    "profile_id": profile_id,
                    "source_id": source_id,
                },
            ).scalar_one()
            connection.execute(
                text(
                    "UPDATE application_packages SET resume_draft_id = :draft_id WHERE id = :package_id"
                ),
                {"draft_id": draft_id, "package_id": package_id},
            )
            connection.execute(
                text(
                    "UPDATE application_preparations SET resume_draft_id = :draft_id "
                    "WHERE application_id = :application_id"
                ),
                {"draft_id": draft_id, "application_id": application_id},
            )
            connection.execute(text("DELETE FROM tailored_resume_drafts WHERE id = :id"), {"id": draft_id})
            assert connection.execute(
                text("SELECT resume_draft_id FROM application_packages WHERE id = :id"),
                {"id": package_id},
            ).scalar_one() is None
            assert connection.execute(
                text("SELECT resume_draft_id FROM application_preparations WHERE application_id = :id"),
                {"id": application_id},
            ).scalar_one() is None
            connection.execute(text("DELETE FROM applications WHERE id = :id"), {"id": application_id})
            assert connection.execute(
                text("SELECT count(*) FROM application_packages WHERE id = :id"), {"id": package_id}
            ).scalar_one() == 0
            assert connection.execute(
                text("SELECT count(*) FROM application_preparations WHERE application_id = :id"),
                {"id": application_id},
            ).scalar_one() == 0

        command.downgrade(config, _PREVIOUS)
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert "application_preparations" not in inspector.get_table_names()
            assert "application_packages" not in inspector.get_table_names()
            assert _revision(connection) == _PREVIOUS
    finally:
        command.upgrade(config, "head")
        engine.dispose()
