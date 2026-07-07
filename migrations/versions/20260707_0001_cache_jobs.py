"""add riot cache and durable jobs

Revision ID: 20260707_0001
Revises:
Create Date: 2026-07-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260707_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "riot_response_cache",
        sa.Column("cache_key", sa.String(length=64), primary_key=True),
        sa.Column("upstream_family", sa.String(length=64), nullable=False),
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column("params_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("headers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stale_until", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_riot_response_cache_family_expires",
        "riot_response_cache",
        ["upstream_family", "expires_at"],
    )
    op.create_index("ix_riot_response_cache_route", "riot_response_cache", ["route"])

    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(length=36), primary_key=True),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("current_wait", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("worker_locked_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_jobs_status_created_at", "jobs", ["status", "created_at"])
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])

    op.create_table(
        "job_events",
        sa.Column("event_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "job_id",
            sa.String(length=36),
            sa.ForeignKey("jobs.job_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_job_events_job_id_event_id", "job_events", ["job_id", "event_id"])


def downgrade() -> None:
    op.drop_index("ix_job_events_job_id_event_id", table_name="job_events")
    op.drop_table("job_events")
    op.drop_index("ix_jobs_created_at", table_name="jobs")
    op.drop_index("ix_jobs_status_created_at", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_riot_response_cache_route", table_name="riot_response_cache")
    op.drop_index("ix_riot_response_cache_family_expires", table_name="riot_response_cache")
    op.drop_table("riot_response_cache")
