"""add admin audit log

Revision ID: 16ab665a7245
Revises: 587517abf9fa
Create Date: 2026-11-18 22:03:51.534209
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "16ab665a7245"
down_revision: str | None = "587517abf9fa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_user_id", sa.Uuid(), nullable=False),
        sa.Column("target_class_id", sa.Uuid(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('create_staff', 'deactivate', 'reactivate', 'change_content_rights',"
            " 'send_reset_link', 'transfer_class', 'change_email', 'export_user', 'delete_user')",
            name=op.f("ck_admin_audit_log_action"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(details) = 'object'", name=op.f("ck_admin_audit_log_details_object")
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_admin_audit_log_actor_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["target_class_id"],
            ["classes.id"],
            name=op.f("fk_admin_audit_log_target_class_id_classes"),
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"], ["users.id"], name=op.f("fk_admin_audit_log_target_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_audit_log")),
    )
    op.create_index(
        op.f("ix_admin_audit_log_actor_id"), "admin_audit_log", ["actor_id"], unique=False
    )
    op.create_index(
        "ix_admin_audit_log_created_at", "admin_audit_log", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_admin_audit_log_target_class_id"),
        "admin_audit_log",
        ["target_class_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_admin_audit_log_target_user_id"),
        "admin_audit_log",
        ["target_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_admin_audit_log_target_user_id"), table_name="admin_audit_log")
    op.drop_index(op.f("ix_admin_audit_log_target_class_id"), table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_log_created_at", table_name="admin_audit_log")
    op.drop_index(op.f("ix_admin_audit_log_actor_id"), table_name="admin_audit_log")
    op.drop_table("admin_audit_log")
