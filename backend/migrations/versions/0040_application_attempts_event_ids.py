"""application attempts and idempotent extension events

Revision ID: 0040
Revises: 0039_application_packages
"""

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039_application_packages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("package_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["package_id"], ["application_packages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_application_attempts_user_id", "application_attempts", ["user_id"])
    op.create_index("ix_application_attempts_application_id", "application_attempts", ["application_id"])
    op.create_index("ix_application_attempts_expires_at", "application_attempts", ["expires_at"])
    op.add_column("application_events", sa.Column("attempt_id", sa.String(length=36), nullable=True))
    op.add_column("application_events", sa.Column("event_id", sa.String(length=36), nullable=True))
    op.create_foreign_key("fk_application_events_attempt_id", "application_events", "application_attempts", ["attempt_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_application_events_attempt_id", "application_events", ["attempt_id"])
    op.create_unique_constraint("uq_application_event_user_event_id", "application_events", ["user_id", "event_id"])
    op.create_index("ix_application_events_timeline", "application_events", ["user_id", "application_id", "created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_application_events_timeline", table_name="application_events")
    op.drop_constraint("uq_application_event_user_event_id", "application_events", type_="unique")
    op.drop_index("ix_application_events_attempt_id", table_name="application_events")
    op.drop_constraint("fk_application_events_attempt_id", "application_events", type_="foreignkey")
    op.drop_column("application_events", "event_id")
    op.drop_column("application_events", "attempt_id")
    op.drop_index("ix_application_attempts_expires_at", table_name="application_attempts")
    op.drop_index("ix_application_attempts_application_id", table_name="application_attempts")
    op.drop_index("ix_application_attempts_user_id", table_name="application_attempts")
    op.drop_table("application_attempts")
