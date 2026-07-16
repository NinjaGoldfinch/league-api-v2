"""prevent duplicate active profile jobs

Revision ID: 20260717_0007
Revises: 20260713_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_0007"
down_revision: str | None = "20260713_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("active_key", sa.String(length=64), nullable=True))
    op.execute(
        """
        create unique index uq_jobs_active_key
        on jobs (active_key)
        where active_key is not null and status in ('queued', 'running')
        """
    )


def downgrade() -> None:
    op.drop_index("uq_jobs_active_key", table_name="jobs")
    op.drop_column("jobs", "active_key")
