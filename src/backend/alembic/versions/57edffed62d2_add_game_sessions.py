"""add game_sessions

Revision ID: 57edffed62d2
Revises: 38493d7f6131
Create Date: 2026-10-07 23:58:44.897483
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "57edffed62d2"
down_revision: str | None = "38493d7f6131"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_sessions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=True),
        sa.Column("target_language", sa.Text(), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), server_default="active", nullable=False),
        sa.Column("end_reason", sa.Text(), nullable=True),
        sa.Column("current_position", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resume_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("shuffles_used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_score", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(state = 'abandoned') = (end_reason IS NOT NULL)",
            name=op.f("ck_game_sessions_end_reason_matches_state"),
        ),
        sa.CheckConstraint(
            "(state = 'completed') = (completed_at IS NOT NULL)",
            name=op.f("ck_game_sessions_completed_at_matches_state"),
        ),
        sa.CheckConstraint(
            "(state = 'paused') = (paused_at IS NOT NULL)",
            name=op.f("ck_game_sessions_paused_at_matches_state"),
        ),
        sa.CheckConstraint(
            "end_reason IN ('quit', 'reset', 'expired', 'class_closed')",
            name=op.f("ck_game_sessions_end_reason"),
        ),
        sa.CheckConstraint(
            "state IN ('active', 'paused', 'completed', 'abandoned')",
            name=op.f("ck_game_sessions_state"),
        ),
        sa.CheckConstraint(
            "target_language IN ('ja', 'zh', 'fr', 'de', 'ko', 'es')",
            name=op.f("ck_game_sessions_target_language"),
        ),
        sa.CheckConstraint(
            "current_position BETWEEN 1 AND 5", name=op.f("ck_game_sessions_current_position")
        ),
        sa.CheckConstraint("difficulty IN (1, 2, 3)", name=op.f("ck_game_sessions_difficulty")),
        sa.CheckConstraint("resume_count >= 0", name=op.f("ck_game_sessions_resume_count")),
        sa.CheckConstraint(
            "shuffles_used BETWEEN 0 AND 2", name=op.f("ck_game_sessions_shuffles_used")
        ),
        sa.CheckConstraint("total_score >= 0", name=op.f("ck_game_sessions_total_score")),
        sa.ForeignKeyConstraint(
            ["class_id", "target_language"],
            ["classes.id", "classes.target_language"],
            name=op.f("fk_game_sessions_class_id_target_language_classes"),
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["users.id"], name=op.f("fk_game_sessions_student_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_game_sessions")),
    )
    op.create_index(
        "ix_game_sessions_open_last_activity_at",
        "game_sessions",
        ["last_activity_at"],
        unique=False,
        postgresql_where=sa.text("state IN ('active', 'paused')"),
    )
    op.create_index(
        op.f("ix_game_sessions_student_id"), "game_sessions", ["student_id"], unique=False
    )
    op.create_index(
        "uq_game_sessions_one_open_per_student",
        "game_sessions",
        ["student_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('active', 'paused')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_game_sessions_one_open_per_student",
        table_name="game_sessions",
        postgresql_where=sa.text("state IN ('active', 'paused')"),
    )
    op.drop_index(op.f("ix_game_sessions_student_id"), table_name="game_sessions")
    op.drop_index(
        "ix_game_sessions_open_last_activity_at",
        table_name="game_sessions",
        postgresql_where=sa.text("state IN ('active', 'paused')"),
    )
    op.drop_table("game_sessions")
