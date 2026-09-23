# ruff: noqa: E501
"""0040 -> 0041 application document-review migration coverage."""

from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.jobs.dedup import job_fingerprint

pytestmark = pytest.mark.integration

_PREVIOUS = "0040"
_CURRENT = "0041"


def _config() -> Config:
    return Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))


def test_0041_upgrade_constraints_and_downgrade() -> None:
    config = _config()
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    marker = uuid4().hex[:10]
    try:
        command.upgrade(config, _PREVIOUS)
        command.downgrade(config, _PREVIOUS)
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE users CASCADE"))
            user_id = connection.execute(
                text("INSERT INTO users (email, name) VALUES (:email, 'Migration') RETURNING id"),
                {"email": f"review-migration-{marker}@example.test"},
            ).scalar_one()
            job_id = connection.execute(text(
                "INSERT INTO jobs (fingerprint, identity_scope, identity_key, title, company, source, "
                "source_url, raw_response, status, visibility, ingest_source) VALUES "
                "(:fingerprint, 'public', :identity, 'Engineer', 'Acme', 'fixture', "
                "'https://example.test/jobs/migration', '{}'::jsonb, 'active', 'public', 'scraper') RETURNING id"
            ), {"fingerprint": job_fingerprint("Acme", "Engineer", None), "identity": f"review:{marker}"}).scalar_one()
            application_id = connection.execute(text(
                "INSERT INTO applications (user_id, job_id, status) VALUES (:user_id, :job_id, 'saved') RETURNING id"
            ), {"user_id": user_id, "job_id": job_id}).scalar_one()
            package_id = connection.execute(text(
                "INSERT INTO application_packages (application_id, revision, content, content_sha256, schema_version, idempotency_key) "
                "VALUES (:application_id, 1, '{}'::jsonb, :hash, 'application-package-v1', 'package') RETURNING id"
            ), {"application_id": application_id, "hash": "a" * 64}).scalar_one()

        command.upgrade(config, _CURRENT)
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert {"application_document_reviews", "application_review_acknowledgments"} <= set(inspector.get_table_names())
            preparation_columns = {column["name"] for column in inspector.get_columns("application_preparations")}
            assert "reviewable_package_id" in preparation_columns
            preparation_fks = {foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys("application_preparations")}
            assert "application_packages" in preparation_fks
            review_fks = {foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys("application_document_reviews")}
            acknowledgment_fks = {foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys("application_review_acknowledgments")}
            assert {"users", "applications", "application_packages"} <= review_fks
            assert {"application_document_reviews", "application_packages"} <= acknowledgment_fks
            indexes = {index["name"]: index for index in inspector.get_indexes("application_document_reviews")}
            active_index = indexes["uq_application_review_active"]
            assert active_index["unique"] is True
            assert active_index["column_names"] == ["user_id", "package_id"]
            predicate = str(active_index.get("dialect_options", {}).get("postgresql_where", "")).casefold()
            assert "state" in predicate
            assert set(re.findall(r"'([^']+)'", predicate)) == {"queued", "running"}
            constraints = {constraint["name"] for constraint in inspector.get_check_constraints("application_document_reviews")}
            assert {"ck_application_review_state", "ck_application_review_outcome"} <= constraints

        with engine.begin() as connection:
            values = {"user_id": user_id, "application_id": application_id, "package_id": package_id, "hash": "a" * 64}
            connection.execute(text(
                "INSERT INTO application_document_reviews "
                "(id, user_id, application_id, package_id, package_content_sha256, review_policy_version, idempotency_key, task_id, state, findings) "
                "VALUES ('review-1', :user_id, :application_id, :package_id, :hash, 'policy', 'one', 'task-1', 'queued', '[]'::jsonb)"
            ), values)
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text(
                        "INSERT INTO application_document_reviews "
                        "(id, user_id, application_id, package_id, package_content_sha256, review_policy_version, idempotency_key, task_id, state, findings) "
                        "VALUES ('review-2', :user_id, :application_id, :package_id, :hash, 'policy', 'two', 'task-2', 'running', '[]'::jsonb)"
                    ), values)
            connection.execute(text(
                "INSERT INTO application_document_reviews "
                "(id, user_id, application_id, package_id, package_content_sha256, review_policy_version, idempotency_key, task_id, state, findings) "
                "VALUES ('review-terminal', :user_id, :application_id, :package_id, :hash, 'policy', 'terminal', 'task-terminal', 'completed', '[]'::jsonb)"
            ), values)
            connection.execute(text(
                "INSERT INTO application_review_acknowledgments (id, review_id, package_id, finding_id, disposition) "
                "VALUES ('ack-1', 'review-1', :package_id, 'finding-1', 'keep_wording')"
            ), {"package_id": package_id})
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text(
                        "INSERT INTO application_review_acknowledgments (id, review_id, package_id, finding_id, disposition) "
                        "VALUES ('ack-2', 'review-1', :package_id, 'finding-1', 'will_revise')"
                    ), {"package_id": package_id})
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text(
                        "INSERT INTO application_document_reviews "
                        "(id, user_id, application_id, package_id, package_content_sha256, review_policy_version, idempotency_key, task_id, state, findings) "
                        "VALUES ('review-invalid', :user_id, :application_id, :package_id, :hash, 'policy', 'three', 'task-3', 'invalid', '[]'::jsonb)"
                    ), values)

        command.downgrade(config, _PREVIOUS)
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert "application_document_reviews" not in tables
            assert "application_review_acknowledgments" not in tables
            assert "reviewable_package_id" not in {column["name"] for column in inspect(connection).get_columns("application_preparations")}
    finally:
        command.upgrade(config, "head")
        engine.dispose()
