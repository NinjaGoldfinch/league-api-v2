"""add durable ranked ladder players

Revision ID: 20260713_0004
Revises: 20260713_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0004"
down_revision: str | None = "20260713_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ranked_ladder_players",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("platform_route", sa.String(16), nullable=False),
        sa.Column("queue", sa.String(32), nullable=False),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("division", sa.String(4), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("puuid", sa.String(128), nullable=False),
        sa.Column("league_points", sa.Integer(), nullable=False),
        sa.Column("wins", sa.Integer(), nullable=False),
        sa.Column("losses", sa.Integer(), nullable=False),
        sa.Column("rank", sa.String(4), nullable=True),
        sa.Column("hot_streak", sa.Boolean(), nullable=False),
        sa.Column("veteran", sa.Boolean(), nullable=False),
        sa.Column("inactive", sa.Boolean(), nullable=False),
        sa.Column("fresh_blood", sa.Boolean(), nullable=False),
        sa.Column("game_name", sa.Text(), nullable=True),
        sa.Column("tag_line", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        """
        create unique index uq_ranked_ladder_players_target_puuid
        on ranked_ladder_players (
            platform_route, queue, tier, coalesce(division, ''), coalesce(page, 0), puuid
        )
        """
    )
    op.create_index("ix_ranked_ladder_players_puuid", "ranked_ladder_players", ["puuid"])
    op.create_index(
        "ix_ranked_ladder_players_target_lp",
        "ranked_ladder_players",
        ["platform_route", "queue", "tier", "division", "page", "league_points"],
    )


def downgrade() -> None:
    op.drop_index("uq_ranked_ladder_players_target_puuid", table_name="ranked_ladder_players")
    op.drop_index("ix_ranked_ladder_players_target_lp", table_name="ranked_ladder_players")
    op.drop_index("ix_ranked_ladder_players_puuid", table_name="ranked_ladder_players")
    op.drop_table("ranked_ladder_players")
