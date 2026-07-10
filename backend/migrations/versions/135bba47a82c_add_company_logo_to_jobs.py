"""add_company_logo_to_jobs

Revision ID: 135bba47a82c
Revises: 0016_job_filter_indexes
Create Date: 2026-07-07 05:39:53.002490
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '135bba47a82c'
down_revision: str | None = '0016_job_filter_indexes'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('company_logo', sa.String(length=1024), nullable=True))


def downgrade() -> None:
    op.drop_column('jobs', 'company_logo')
    op.create_index(op.f('ix_user_job_imports_user_imported'), 'user_job_imports', ['user_id', 'imported_at'], unique=False)
    op.create_index(op.f('ix_user_job_imports_job_id'), 'user_job_imports', ['job_id'], unique=False)
    op.alter_column('scrape_queries', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               existing_server_default=sa.text('now()'))
    op.alter_column('scrape_queries', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               existing_server_default=sa.text('now()'))
    op.create_index(op.f('ix_jobs_visibility_status_recency'), 'jobs', ['visibility', 'status', sa.literal_column('COALESCE(last_seen_at, scraped_at, fetched_at)')], unique=False)
    op.create_index(op.f('ix_jobs_title_trgm'), 'jobs', ['title'], unique=False, postgresql_ops={'title': 'gin_trgm_ops'}, postgresql_using='gin')
    op.create_index(op.f('ix_jobs_private_owner'), 'jobs', ['created_by_user_id', 'status'], unique=False, postgresql_where="((visibility)::text = 'private'::text)")
    op.create_index(op.f('ix_jobs_job_type_lower'), 'jobs', [sa.literal_column('lower(job_type::text)')], unique=False)
    op.create_index(op.f('ix_jobs_is_remote'), 'jobs', ['is_remote'], unique=False)
    op.create_index(op.f('ix_jobs_fetched_at_desc'), 'jobs', [sa.literal_column('fetched_at DESC')], unique=False)
    op.create_index(op.f('ix_jobs_embedding_hnsw'), 'jobs', ['description_embedding'], unique=False, postgresql_ops={'description_embedding': 'vector_cosine_ops'}, postgresql_using='hnsw')
    op.create_index(op.f('ix_jobs_company_trgm'), 'jobs', ['company'], unique=False, postgresql_ops={'company': 'gin_trgm_ops'}, postgresql_using='gin')
    op.drop_column('jobs', 'company_logo')
    op.drop_index(op.f('ix_application_form_sessions_user_id'), table_name='application_form_sessions')
    op.drop_index(op.f('ix_application_form_sessions_job_id'), table_name='application_form_sessions')
    op.drop_index(op.f('ix_application_form_sessions_application_id'), table_name='application_form_sessions')
    op.create_index(op.f('ix_form_sessions_user_id'), 'application_form_sessions', ['user_id'], unique=False)
    op.create_index(op.f('ix_form_sessions_application_id'), 'application_form_sessions', ['application_id'], unique=False)
    op.drop_index(op.f('ix_application_form_fields_form_session_id'), table_name='application_form_fields')
    op.create_index(op.f('ix_form_fields_session_id'), 'application_form_fields', ['form_session_id'], unique=False)
    op.create_index(op.f('ix_application_events_user_app_created'), 'application_events', ['user_id', 'application_id', 'created_at'], unique=False)
    # ### end Alembic commands ###
