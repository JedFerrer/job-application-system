"""Add durable per-query source freshness state.

Revision ID: 0038_source_refresh_state
Revises: 0037_scoped_job_identity
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0038_source_refresh_state"
down_revision = "0037_scoped_job_identity"
branch_labels = None
depends_on = None

_INITIAL_DUE_WINDOW_SECONDS = 24 * 60 * 60


def _initial_due_at(*, query_id: str, source: str, now: datetime) -> datetime:
    """Migration-safe counterpart of runtime source-state staggering."""
    utc_now = now.astimezone(UTC)
    window_start = utc_now.replace(hour=0, minute=0, second=0, microsecond=0)
    identity = f"{query_id}:{source.strip().casefold()}".encode()
    offset = int.from_bytes(hashlib.sha256(identity).digest()[:8], "big") % _INITIAL_DUE_WINDOW_SECONDS
    return window_start + timedelta(seconds=offset)


def upgrade() -> None:
    op.create_table(
        "scrape_query_source_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "scrape_query_id",
            sa.String(36),
            sa.ForeignKey("scrape_queries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_successful_complete_at", sa.DateTime(timezone=True)),
        sa.Column("next_due_at", sa.DateTime(timezone=True)),
        sa.Column("latest_status", sa.String(32), nullable=False, server_default="unavailable"),
        sa.Column("reason_code", sa.String(64)),
        sa.Column("completeness", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("fetched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accepted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("effective_country", sa.String(64)),
        sa.Column("effective_result_limit", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("scrape_query_id", "source", name="uq_scrape_query_source"),
    )
    op.create_index("ix_scrape_query_source_states_query", "scrape_query_source_states", ["scrape_query_id"])
    op.create_index("ix_scrape_query_source_states_due", "scrape_query_source_states", ["next_due_at"])

    # Legacy aggregate timestamps cannot prove a source-specific complete
    # search. Backfill only source names explicitly persisted on each query.
    bind = op.get_bind()
    now = datetime.now(UTC)
    rows = bind.execute(sa.text("SELECT id, sources FROM scrape_queries"))
    values: list[dict[str, object]] = []
    for query_id, sources in rows:
        for source in sorted({str(item) for item in (sources or []) if str(item).strip()}):
            values.append(
                {
                    "id": str(uuid4()),
                    "scrape_query_id": str(query_id),
                    "source": source,
                    "next_due_at": _initial_due_at(
                        query_id=str(query_id), source=source, now=now
                    ),
                    "latest_status": "unavailable",
                    "completeness": "unknown",
                    "fetched_count": 0,
                    "accepted_count": 0,
                    "rejected_count": 0,
                }
            )
    if values:
        bind.execute(
            sa.table(
                "scrape_query_source_states",
                sa.column("id"),
                sa.column("scrape_query_id"),
                sa.column("source"),
                sa.column("next_due_at"),
                sa.column("latest_status"),
                sa.column("completeness"),
                sa.column("fetched_count"),
                sa.column("accepted_count"),
                sa.column("rejected_count"),
            ).insert(),
            values,
        )


def downgrade() -> None:
    op.drop_table("scrape_query_source_states")
