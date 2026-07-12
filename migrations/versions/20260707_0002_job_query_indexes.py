"""add job query indexes

Revision ID: 20260707_0002
Revises: 20260707_0001
Create Date: 2026-07-07 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260707_0002"
down_revision: str | None = "20260707_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_jobs_type_status_created_job",
        "jobs",
        ["job_type", "status", "created_at", "job_id"],
    )
    op.execute(
        """
        create index ix_jobs_profile_identity_routes
        on jobs (
            job_type,
            lower(params ->> 'game_name'),
            lower(params ->> 'tag_line'),
            (params ->> 'account_regional_route'),
            (params ->> 'platform_route'),
            (params ->> 'regional_route'),
            (params ->> 'match_count'),
            status,
            created_at,
            job_id
        )
        where job_type = 'profile_fetch'
        """
    )


def downgrade() -> None:
    op.execute("drop index if exists ix_jobs_profile_identity_routes")
    op.drop_index("ix_jobs_type_status_created_job", table_name="jobs")
