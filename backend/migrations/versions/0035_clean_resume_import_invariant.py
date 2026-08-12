"""Reset obsolete development resume data and enforce one profile per resume.

The reset is intentionally irreversible. It affects only resume-domain rows;
saved tailored snapshots retain their JSON content and lose their optional
profile reference through the existing ``ON DELETE SET NULL`` foreign key.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_clean_resume_invariant"
down_revision: str | None = "0034_source_resume_contents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Candidate profiles reference resumes, while tailored snapshots reference
    # profiles with ON DELETE SET NULL. Delete in dependency order; source
    # content then cascades from resumes.
    op.execute(sa.text("DELETE FROM candidate_profiles"))
    op.execute(sa.text("DELETE FROM resumes"))
    op.create_unique_constraint(
        "uq_candidate_profiles_resume", "candidate_profiles", ["resume_id"]
    )


def downgrade() -> None:
    # Deleted development data is not reconstructed on downgrade.
    op.drop_constraint(
        "uq_candidate_profiles_resume", "candidate_profiles", type_="unique"
    )
