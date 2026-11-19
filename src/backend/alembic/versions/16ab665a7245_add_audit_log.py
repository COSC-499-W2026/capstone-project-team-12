"""add audit log

Revision ID: 16ab665a7245
Revises: 3b5adf42e8c1
Create Date: 2026-11-18 22:03:51.534209
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "16ab665a7245"
down_revision: str | None = "3b5adf42e8c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("ip_address", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "action IN ('user_created', 'user_deactivated', 'user_reactivated', 'user_updated',"
            " 'permissions_changed', 'class_transferred')",
            name=op.f("ck_audit_log_action"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(details) = 'object'", name=op.f("ck_audit_log_details_object")
        ),
        sa.CheckConstraint(
            "result IN ('success', 'failure', 'denied')", name=op.f("ck_audit_log_result")
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_audit_log_actor_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(op.f("ix_audit_log_actor_id"), "audit_log", ["actor_id"], unique=False)
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"], unique=False)
    op.create_index(op.f("ix_audit_log_target_id"), "audit_log", ["target_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_log_target_id"), table_name="audit_log")
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_actor_id"), table_name="audit_log")
    op.drop_table("audit_log")
