"""add access_codes and class_enrollments

Revision ID: 38493d7f6131
Revises: 1a8d5899a56f
Create Date: 2026-10-06 13:35:46.866625
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "38493d7f6131"
down_revision: str | None = "1a8d5899a56f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "access_codes",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("uses_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("code ~ '^[A-Z0-9]{8}$'", name=op.f("ck_access_codes_code_format")),
        sa.CheckConstraint(
            "max_uses IS NULL OR max_uses > 0", name=op.f("ck_access_codes_max_uses_positive")
        ),
        sa.CheckConstraint(
            "uses_count >= 0 AND (max_uses IS NULL OR uses_count <= max_uses)",
            name=op.f("ck_access_codes_uses_within_max"),
        ),
        sa.ForeignKeyConstraint(
            ["class_id"], ["classes.id"], name=op.f("fk_access_codes_class_id_classes")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_access_codes_created_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_access_codes")),
        sa.UniqueConstraint("code", name=op.f("uq_access_codes_code")),
    )
    op.create_index(op.f("ix_access_codes_class_id"), "access_codes", ["class_id"], unique=False)
    op.create_table(
        "class_enrollments",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("access_code_id", sa.Uuid(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("end_reason", sa.Text(), nullable=True),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "end_reason IN ('left', 'removed', 'archived')",
            name=op.f("ck_class_enrollments_end_reason"),
        ),
        sa.CheckConstraint(
            "language IN ('ja', 'zh', 'fr', 'de', 'ko', 'es')",
            name=op.f("ck_class_enrollments_language"),
        ),
        sa.CheckConstraint(
            "is_active = (end_reason IS NULL)",
            name=op.f("ck_class_enrollments_end_reason_matches_status"),
        ),
        sa.ForeignKeyConstraint(
            ["access_code_id"],
            ["access_codes.id"],
            name=op.f("fk_class_enrollments_access_code_id_access_codes"),
        ),
        sa.ForeignKeyConstraint(
            ["class_id", "language"],
            ["classes.id", "classes.target_language"],
            name=op.f("fk_class_enrollments_class_id_language_classes"),
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["users.id"], name=op.f("fk_class_enrollments_student_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_class_enrollments")),
        sa.UniqueConstraint(
            "student_id", "class_id", name=op.f("uq_class_enrollments_student_id_class_id")
        ),
    )
    op.create_index(
        op.f("ix_class_enrollments_class_id"), "class_enrollments", ["class_id"], unique=False
    )
    op.create_index(
        "uq_class_enrollments_one_active_per_language",
        "class_enrollments",
        ["student_id", "language"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_class_enrollments_one_active_per_language",
        table_name="class_enrollments",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_index(op.f("ix_class_enrollments_class_id"), table_name="class_enrollments")
    op.drop_table("class_enrollments")
    op.drop_index(op.f("ix_access_codes_class_id"), table_name="access_codes")
    op.drop_table("access_codes")
