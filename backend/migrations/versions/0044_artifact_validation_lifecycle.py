"""artifact validation lifecycle

Revision ID: 0044
Revises: 0043
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("application_artifacts", sa.Column("task_id", sa.String(36), nullable=True))
    op.add_column("application_artifacts", sa.Column("validation_attempt", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("application_artifacts", sa.Column("validation_state_changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))


def downgrade() -> None:
    op.drop_column("application_artifacts", "validation_state_changed_at")
    op.drop_column("application_artifacts", "validation_attempt")
    op.drop_column("application_artifacts", "task_id")
