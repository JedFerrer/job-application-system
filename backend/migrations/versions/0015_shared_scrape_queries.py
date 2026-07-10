"""Shared scrape queries (Phase 5)

Revision ID: 0015_shared_scrape_queries
Revises: 0014_feed_separation
Create Date: 2026-06-30 01:00:00.000000

- NEW: scrape_queries — one row per unique normalised search
- NEW: user_scrape_query_subscriptions — links user prefs to queries
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "0015_shared_scrape_queries"
down_revision: str | None = "0014_feed_separation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scrape_queries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("search_term", sa.String(255), nullable=False),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("is_remote", sa.Boolean(), nullable=True),
        sa.Column("hours_old", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("results_wanted", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("sources", ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column(
            "normalized_hash", sa.String(64), unique=True, nullable=False, index=True
        ),
        sa.Column("last_scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_scrape_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "active_user_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "user_scrape_query_subscriptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "preference_id",
            sa.Integer(),
            sa.ForeignKey("user_job_preferences.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "scrape_query_id",
            sa.String(36),
            sa.ForeignKey("scrape_queries.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id",
            "preference_id",
            "scrape_query_id",
            name="uq_user_pref_query_sub",
        ),
    )


def downgrade() -> None:
    op.drop_table("user_scrape_query_subscriptions")
    op.drop_table("scrape_queries")
