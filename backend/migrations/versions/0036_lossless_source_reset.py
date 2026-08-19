"""Reset tailored snapshots and require a verified source-content anchor."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_lossless_source_reset"
down_revision: str | None = "0035_clean_resume_invariant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Tailored snapshots are derived artifacts; never preserve them across the
    # lossless source contract reset. Source/profile rows remain intact.
    op.execute(sa.text("DELETE FROM tailored_resume_drafts"))
    op.drop_constraint(
        "tailored_resume_drafts_source_profile_id_fkey",
        "tailored_resume_drafts",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "tailored_resume_drafts_source_profile_id_fkey",
        "tailored_resume_drafts",
        "candidate_profiles",
        ["source_profile_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.add_column(
        "tailored_resume_drafts",
        sa.Column("source_resume_content_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "tailored_resume_drafts_source_resume_content_id_fkey",
        "tailored_resume_drafts",
        "source_resume_contents",
        ["source_resume_content_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column("tailored_resume_drafts", "source_profile_id", nullable=False)
    op.alter_column("tailored_resume_drafts", "source_resume_content_id", nullable=False)


def downgrade() -> None:
    op.drop_constraint(
        "tailored_resume_drafts_source_resume_content_id_fkey",
        "tailored_resume_drafts",
        type_="foreignkey",
    )
    op.drop_column("tailored_resume_drafts", "source_resume_content_id")
    op.drop_constraint(
        "tailored_resume_drafts_source_profile_id_fkey",
        "tailored_resume_drafts",
        type_="foreignkey",
    )
    op.alter_column("tailored_resume_drafts", "source_profile_id", nullable=True)
    op.create_foreign_key(
        "tailored_resume_drafts_source_profile_id_fkey",
        "tailored_resume_drafts",
        "candidate_profiles",
        ["source_profile_id"],
        ["id"],
        ondelete="SET NULL",
    )
