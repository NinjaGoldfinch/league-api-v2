"""add durable immutable match storage

Revision ID: 20260713_0003
Revises: 20260707_0002
Create Date: 2026-07-13 08:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260713_0003"
down_revision: str | None = "20260707_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_riot_response_cache_stale_until",
        "riot_response_cache",
        ["stale_until"],
    )
    op.create_table(
        "riot_matches",
        sa.Column("match_id", sa.String(length=64), primary_key=True),
        sa.Column("regional_route", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("game_creation", sa.BigInteger(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_riot_matches_game_creation", "riot_matches", ["game_creation"])
    op.create_table(
        "player_matches",
        sa.Column("puuid", sa.String(length=128), nullable=False),
        sa.Column(
            "match_id",
            sa.String(length=64),
            sa.ForeignKey("riot_matches.match_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("puuid", "match_id"),
    )
    op.create_index(
        "ix_player_matches_puuid_discovered",
        "player_matches",
        ["puuid", "discovered_at"],
    )
    op.execute(
        """
        insert into riot_matches (
            match_id, regional_route, payload, game_creation, fetched_at
        )
        select
            match_entry.key,
            coalesce(j.params ->> 'regional_route', 'sea'),
            match_entry.value,
            case
                when match_entry.value -> 'info' ->> 'gameCreation' ~ '^[0-9]+$'
                then (match_entry.value -> 'info' ->> 'gameCreation')::bigint
                else null
            end,
            coalesce(j.finished_at, j.created_at)
        from jobs j
        cross join lateral jsonb_each(coalesce(j.result -> 'matches', '{}'::jsonb)) match_entry
        where j.status = 'succeeded'
        on conflict (match_id) do nothing
        """
    )
    op.execute(
        """
        insert into player_matches (puuid, match_id, discovered_at)
        select distinct
            j.result -> 'account' ->> 'puuid',
            match_entry.key,
            coalesce(j.finished_at, j.created_at)
        from jobs j
        cross join lateral jsonb_each(coalesce(j.result -> 'matches', '{}'::jsonb)) match_entry
        where j.job_type = 'profile_fetch'
          and j.status = 'succeeded'
          and nullif(j.result -> 'account' ->> 'puuid', '') is not null
        on conflict (puuid, match_id) do nothing
        """
    )


def downgrade() -> None:
    op.drop_index("ix_player_matches_puuid_discovered", table_name="player_matches")
    op.drop_table("player_matches")
    op.drop_index("ix_riot_matches_game_creation", table_name="riot_matches")
    op.drop_table("riot_matches")
    op.drop_index("ix_riot_response_cache_stale_until", table_name="riot_response_cache")
