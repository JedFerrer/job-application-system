"""Feed separation & privacy (Phase 4)

Revision ID: 0014_feed_separation
Revises: 0013_form_sessions
Create Date: 2026-06-30 00:00:00.000000

- jobs: add visibility, created_by_user_id, ingest_source, status, timestamps;
        drop is_archived (per-user archiving moves to user_jobs.status).
- user_jobs: add relationship_source, import_method, match_score, match_reason,
             status, viewed_at, saved_at, ignored_at.
- NEW: user_job_imports — private raw import data scoped per user.
- NEW: job_source_links — platform-native IDs for cross-platform dedup.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0014_feed_separation"
down_revision: str | None = "0013_form_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── jobs: drop is_archived, add new columns ────────────────────────
    op.drop_index("ix_jobs_is_archived", table_name="jobs")
    op.drop_column("jobs", "is_archived")

    with op.batch_alter_table("jobs") as bt:
        bt.add_column(
            sa.Column(
                "visibility",
                sa.String(20),
                nullable=False,
                server_default="public",
            )
        )
        bt.add_column(
            sa.Column(
                "created_by_user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        bt.add_column(
            sa.Column(
                "ingest_source",
                sa.String(50),
                nullable=False,
                server_default="scraper",
            )
        )
        bt.add_column(
            sa.Column(
                "status",
                sa.String(30),
                nullable=False,
                server_default="active",
            )
        )
        bt.add_column(
            sa.Column(
                "first_seen_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            )
        )
        bt.add_column(
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True)
        )
        bt.add_column(
            sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=True)
        )
        bt.add_column(
            sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True)
        )
        bt.add_column(
            sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True)
        )

    op.create_index(
        "ix_jobs_visibility_status_recency",
        "jobs",
        ["visibility", "status", sa.text("COALESCE(last_seen_at, scraped_at, fetched_at)")],
    )
    op.create_index("ix_jobs_visibility", "jobs", ["visibility"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index(
        "ix_jobs_private_owner",
        "jobs",
        ["created_by_user_id", "status"],
        postgresql_where=sa.text("visibility = 'private'"),
    )

    # ── user_jobs: add relationship / match / status columns ───────────
    with op.batch_alter_table("user_jobs") as bt:
        bt.add_column(
            sa.Column(
                "relationship_source",
                sa.String(50),
                nullable=False,
                server_default="match",
            )
        )
        bt.add_column(
            sa.Column("import_method", sa.String(50), nullable=True)
        )
        bt.add_column(
            sa.Column("match_score", sa.Numeric, nullable=True)
        )
        bt.add_column(
            sa.Column("match_reason", JSONB, nullable=True)
        )
        bt.add_column(
            sa.Column(
                "status",
                sa.String(30),
                nullable=False,
                server_default="new",
            )
        )
        bt.add_column(
            sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=True)
        )
        bt.add_column(
            sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True)
        )
        bt.add_column(
            sa.Column("ignored_at", sa.DateTime(timezone=True), nullable=True)
        )

    op.create_index(
        "ix_user_jobs_user_relationship",
        "user_jobs",
        ["user_id", "relationship_source", "created_at"],
    )
    op.create_index(
        "ix_user_jobs_user_status", "user_jobs", ["user_id", "status"]
    )
    op.create_index("ix_user_jobs_match_score", "user_jobs", ["user_id", "match_score"])

    # ── user_job_imports ───────────────────────────────────────────────
    op.create_table(
        "user_job_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_job_user_id", sa.Integer(), nullable=False),
        sa.Column("user_job_job_id", sa.Integer(), nullable=False),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("import_method", sa.String(50), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("raw_input", sa.Text(), nullable=True),
        sa.Column("raw_payload", JSONB(), nullable=True),
        sa.Column("raw_html_hash", sa.String(255), nullable=True),
        sa.Column("parsed_title", sa.String(500), nullable=True),
        sa.Column("parsed_company", sa.String(500), nullable=True),
        sa.Column("parsed_location", sa.String(500), nullable=True),
        sa.Column("parsed_salary", sa.String(255), nullable=True),
        sa.Column("parse_confidence", sa.Numeric(), nullable=True),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_user_job_imports_user_id", "user_job_imports", ["user_id"]
    )
    op.create_index(
        "ix_user_job_imports_user_imported",
        "user_job_imports",
        ["user_id", "imported_at"],
    )
    op.create_index(
        "ix_user_job_imports_job_id", "user_job_imports", ["job_id"]
    )
    # Composite FK to user_jobs(user_id, job_id) — raw SQL for correctness
    op.execute(
        "ALTER TABLE user_job_imports "
        "ADD CONSTRAINT fk_user_job_imports_user_jobs "
        "FOREIGN KEY (user_job_user_id, user_job_job_id) "
        "REFERENCES user_jobs(user_id, job_id) ON DELETE CASCADE"
    )

    # ── job_source_links ───────────────────────────────────────────────
    op.create_table(
        "job_source_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("source_job_id", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("external_apply_url", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(30), nullable=False, server_default="active"
        ),
        sa.Column("raw_payload", JSONB(), nullable=True),
    )
    op.create_index(
        "ix_job_source_links_job_id", "job_source_links", ["job_id"]
    )
    op.create_unique_constraint(
        "uq_source_job_id", "job_source_links", ["source", "source_job_id"]
    )


def downgrade() -> None:
    # ── job_source_links ───────────────────────────────────────────────
    op.drop_table("job_source_links")

    # ── user_job_imports ───────────────────────────────────────────────
    op.drop_table("user_job_imports")

    # ── user_jobs: remove new columns ──────────────────────────────────
    with op.batch_alter_table("user_jobs") as bt:
        bt.drop_index("ix_user_jobs_match_score")
        bt.drop_index("ix_user_jobs_user_status")
        bt.drop_index("ix_user_jobs_user_relationship")
        bt.drop_column("ignored_at")
        bt.drop_column("saved_at")
        bt.drop_column("viewed_at")
        bt.drop_column("status")
        bt.drop_column("match_reason")
        bt.drop_column("match_score")
        bt.drop_column("import_method")
        bt.drop_column("relationship_source")

    # ── jobs: remove new columns, restore is_archived ──────────────────
    op.drop_index("ix_jobs_private_owner", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_visibility", table_name="jobs")
    op.drop_index("ix_jobs_visibility_status_recency", table_name="jobs")

    with op.batch_alter_table("jobs") as bt:
        bt.drop_column("expired_at")
        bt.drop_column("imported_at")
        bt.drop_column("scraped_at")
        bt.drop_column("last_seen_at")
        bt.drop_column("first_seen_at")
        bt.drop_column("status")
        bt.drop_column("ingest_source")
        bt.drop_column("created_by_user_id")
        bt.drop_column("visibility")

    op.add_column(
        "jobs",
        sa.Column(
            "is_archived",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    op.create_index("ix_jobs_is_archived", "jobs", ["is_archived"])
