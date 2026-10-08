"""add users, classes and class_staff

Revision ID: 1a8d5899a56f
Revises:
Create Date: 2026-10-06 13:01:35.733779
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1a8d5899a56f"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "classes",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("semester", sa.Text(), nullable=False),
        sa.Column("target_language", sa.Text(), nullable=False),
        sa.Column(
            "leaderboard_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "semester ~ '^[0-9]{4}(W1|W2|S1|S2)$'", name=op.f("ck_classes_semester_format")
        ),
        sa.CheckConstraint(
            "target_language IN ('ja', 'zh', 'fr', 'de', 'ko', 'es')",
            name=op.f("ck_classes_target_language"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_classes")),
        sa.UniqueConstraint("id", "target_language", name=op.f("uq_classes_id_target_language")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "can_manage_content", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("current_class_id", sa.Uuid(), nullable=True),
        sa.Column("preferred_language", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "preferred_language IN ('ja', 'zh', 'fr', 'de', 'ko', 'es')",
            name=op.f("ck_users_preferred_language"),
        ),
        sa.CheckConstraint("role IN ('student', 'staff', 'admin')", name=op.f("ck_users_role")),
        sa.CheckConstraint(
            "char_length(username) BETWEEN 3 AND 30", name=op.f("ck_users_username_length")
        ),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lowercase")),
        sa.CheckConstraint("username = btrim(username)", name=op.f("ck_users_username_trimmed")),
        sa.CheckConstraint(
            "NOT can_manage_content OR role = 'staff'",
            name=op.f("ck_users_content_rights_staff_only"),
        ),
        sa.ForeignKeyConstraint(
            ["current_class_id"], ["classes.id"], name=op.f("fk_users_current_class_id_classes")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(
        "uq_users_username_lower", "users", [sa.literal_column("lower(username)")], unique=True
    )
    op.create_table(
        "class_staff",
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("role IN ('owner', 'assistant')", name=op.f("ck_class_staff_role")),
        sa.ForeignKeyConstraint(
            ["class_id"], ["classes.id"], name=op.f("fk_class_staff_class_id_classes")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_class_staff_user_id_users")
        ),
        sa.PrimaryKeyConstraint("class_id", "user_id", name=op.f("pk_class_staff")),
    )
    op.create_index(op.f("ix_class_staff_user_id"), "class_staff", ["user_id"], unique=False)
    op.create_index(
        "uq_class_staff_one_owner",
        "class_staff",
        ["class_id"],
        unique=True,
        postgresql_where=sa.text("role = 'owner'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_class_staff_one_owner",
        table_name="class_staff",
        postgresql_where=sa.text("role = 'owner'"),
    )
    op.drop_index(op.f("ix_class_staff_user_id"), table_name="class_staff")
    op.drop_table("class_staff")
    op.drop_index("uq_users_username_lower", table_name="users")
    op.drop_table("users")
    op.drop_table("classes")
