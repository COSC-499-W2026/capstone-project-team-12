"""add audio_assets and feedback_audio

Revision ID: 434124c953bc
Revises: b490694173b4
Create Date: 2026-10-13 21:46:59.722328
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "434124c953bc"
down_revision: str | None = "b490694173b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedback_audio",
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "code IN ('NAV_START', 'NAV_GETTING_CLOSER', 'NAV_GETTING_FARTHER', 'NAV_KEEP_GOING',"
            " 'NAV_VERY_CLOSE', 'NAV_ARRIVED', 'NAV_GPS_WEAK', 'VERIFY_START', 'VERIFY_SUCCESS',"
            " 'VERIFY_UNCLEAR', 'VERIFY_NO_MATCH', 'VERIFY_EXHAUSTED', 'SESSION_COMPLETE')",
            name=op.f("ck_feedback_audio_code"),
        ),
        sa.CheckConstraint(
            "language IN ('ja', 'zh', 'fr', 'de', 'ko', 'es')",
            name=op.f("ck_feedback_audio_language"),
        ),
        sa.PrimaryKeyConstraint("code", "language", name=op.f("pk_feedback_audio")),
        sa.UniqueConstraint("file_path", name=op.f("uq_feedback_audio_file_path")),
    )
    op.create_table(
        "audio_assets",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("clue_id", sa.Uuid(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), server_default="uploaded", nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("duration_seconds", sa.Double(), nullable=False),
        sa.Column("file_size_kb", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_type IN ('uploaded', 'ai_generated')", name=op.f("ck_audio_assets_source_type")
        ),
        sa.CheckConstraint("duration_seconds > 0", name=op.f("ck_audio_assets_duration_seconds")),
        sa.CheckConstraint("file_size_kb > 0", name=op.f("ck_audio_assets_file_size_kb")),
        sa.ForeignKeyConstraint(
            ["clue_id"], ["clues.id"], name=op.f("fk_audio_assets_clue_id_clues")
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["users.id"], name=op.f("fk_audio_assets_uploaded_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audio_assets")),
        sa.UniqueConstraint("file_path", name=op.f("uq_audio_assets_file_path")),
    )
    op.create_index(op.f("ix_audio_assets_clue_id"), "audio_assets", ["clue_id"], unique=False)
    op.create_index(
        "uq_audio_assets_one_primary_per_clue",
        "audio_assets",
        ["clue_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_audio_assets_one_primary_per_clue",
        table_name="audio_assets",
        postgresql_where=sa.text("is_primary"),
    )
    op.drop_index(op.f("ix_audio_assets_clue_id"), table_name="audio_assets")
    op.drop_table("audio_assets")
    op.drop_table("feedback_audio")
