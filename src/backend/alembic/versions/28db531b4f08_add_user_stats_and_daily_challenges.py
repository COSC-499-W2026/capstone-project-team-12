"""Add user stats and daily challenges.

Revision ID: 28db531b4f08
Revises: 9d4b6e2a1f73
Create Date: 2026-11-14 10:27:06.374957
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "28db531b4f08"
down_revision: str | None = "9d4b6e2a1f73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the A13 streak and daily challenge tables."""

    op.create_table(
        "user_stats",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "current_streak_days",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "best_streak_days",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_active_date", sa.Date(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "current_streak_days >= 0",
            name=op.f("ck_user_stats_current_streak_days_nonnegative"),
        ),
        sa.CheckConstraint(
            "best_streak_days >= 0",
            name=op.f("ck_user_stats_best_streak_at_least_current"),
        ),
        sa.CheckConstraint(
            "best_streak_days >= current_streak_days",
            name=op.f("ck_user_stats_best_streak_at_least_current"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_stats_user_id_users"),
        ),
        sa.PrimaryKeyConstraint(
            "user_id",
            name=op.f("pk_user_stats"),
        ),
    )

    op.create_table(
        "daily_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("challenge_date", sa.Date(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("challenge_type", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("description_key", sa.Text(), nullable=False),
        sa.Column("reward_points", sa.Integer(), nullable=False),
        sa.Column(
            "progress_current",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("progress_required", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "slot BETWEEN 1 AND 3",
            name=op.f("ck_daily_challenges_slot_range"),
        ),
        sa.CheckConstraint(
            "challenge_type IN ('completion', 'difficulty')",
            name=op.f("ck_daily_challenges_challenge_type"),
        ),
        sa.CheckConstraint(
            "reward_points IN (50, 100)",
            name=op.f("ck_daily_challenges_reward_points_allowed"),
        ),
        sa.CheckConstraint(
            "progress_required > 0",
            name=op.f("ck_daily_challenges_progress_required_positive"),
        ),
        sa.CheckConstraint(
            "progress_current >= 0 AND progress_current <= progress_required",
            name=op.f("ck_daily_challenges_progress_within_required"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_daily_challenges_user_id_users"),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_daily_challenges"),
        ),
        sa.UniqueConstraint(
            "user_id",
            "challenge_date",
            "slot",
            name=op.f("uq_daily_challenges_user_id_challenge_date_slot"),
        ),
    )

    op.create_index(
        op.f("ix_daily_challenges_user_id"),
        "daily_challenges",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the A13 tables."""

    op.drop_index(
        op.f("ix_daily_challenges_user_id"),
        table_name="daily_challenges",
    )
    op.drop_table("daily_challenges")
    op.drop_table("user_stats")
