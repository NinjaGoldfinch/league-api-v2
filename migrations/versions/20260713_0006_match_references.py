"""add match references discovered before detail fetching

Revision ID: 20260713_0006
Revises: 20260713_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0006"
down_revision: str | None = "20260713_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "player_match_references",
        sa.Column("puuid", sa.String(128), nullable=False),
        sa.Column("match_id", sa.String(64), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("puuid", "match_id"),
    )
    op.create_index(
        "ix_player_match_references_match_id",
        "player_match_references",
        ["match_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_player_match_references_match_id",
        table_name="player_match_references",
    )
    op.drop_table("player_match_references")
