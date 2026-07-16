"""add PUUID to Riot ID associations

Revision ID: 20260713_0005
Revises: 20260713_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0005"
down_revision: str | None = "20260713_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "player_identities",
        sa.Column("puuid", sa.String(128), primary_key=True),
        sa.Column("game_name", sa.String(128), nullable=False),
        sa.Column("tag_line", sa.String(32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_player_identities_riot_id",
        "player_identities",
        [sa.text("lower(game_name)"), sa.text("lower(tag_line)")],
    )


def downgrade() -> None:
    op.drop_index("ix_player_identities_riot_id", table_name="player_identities")
    op.drop_table("player_identities")
