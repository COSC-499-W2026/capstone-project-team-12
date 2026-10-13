"""add email_tokens and email_outbox

Revision ID: e91689d8206b
Revises: 1d35b6cae138
Create Date: 2026-10-12 06:36:42.876366
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e91689d8206b"
down_revision: str | None = "1d35b6cae138"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("to_email", sa.Text(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'sent') = (sent_at IS NOT NULL)",
            name=op.f("ck_email_outbox_sent_at_matches_status"),
        ),
        sa.CheckConstraint(
            "(template IN ('reset_password', 'set_password', 'change_email',"
            " 'email_changed_notice') AND priority = 1)"
            " OR (template IN ('verify_email', 'ta_invitation') AND priority = 2)"
            " OR template NOT IN ('verify_email', 'reset_password', 'set_password',"
            " 'change_email', 'email_changed_notice', 'ta_invitation')",
            name=op.f("ck_email_outbox_priority_matches_template"),
        ),
        sa.CheckConstraint(
            "NOT (params ?| ARRAY['token', 'token_hash'])",
            name=op.f("ck_email_outbox_params_have_no_token"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(params) = 'object'", name=op.f("ck_email_outbox_params_object")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed')", name=op.f("ck_email_outbox_status")
        ),
        sa.CheckConstraint(
            "template IN ('verify_email', 'reset_password', 'set_password', 'change_email',"
            " 'email_changed_notice', 'ta_invitation')",
            name=op.f("ck_email_outbox_template"),
        ),
        sa.CheckConstraint("attempts BETWEEN 0 AND 5", name=op.f("ck_email_outbox_attempts")),
        sa.CheckConstraint(
            "to_email = lower(to_email)", name=op.f("ck_email_outbox_to_email_lowercase")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_email_outbox_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_outbox")),
    )
    op.create_index(
        "ix_email_outbox_pending_priority_created",
        "email_outbox",
        ["priority", "created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_email_outbox_to_email_template_created",
        "email_outbox",
        ["to_email", "template", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_email_outbox_one_pending_reset",
        "email_outbox",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("template = 'reset_password' AND status = 'pending'"),
    )
    op.create_table(
        "email_tokens",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("new_email", sa.Text(), nullable=True),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(purpose = 'change_email') = (new_email IS NOT NULL)",
            name=op.f("ck_email_tokens_new_email_matches_purpose"),
        ),
        sa.CheckConstraint(
            "purpose IN ('verify_email', 'reset_password', 'set_password', 'change_email')",
            name=op.f("ck_email_tokens_purpose"),
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_email_tokens_token_hash_sha256")
        ),
        sa.CheckConstraint(
            "new_email IS NULL OR new_email = lower(new_email)",
            name=op.f("ck_email_tokens_new_email_lowercase"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_email_tokens_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_email_tokens_token_hash")),
    )
    op.create_index(op.f("ix_email_tokens_user_id"), "email_tokens", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_email_tokens_user_id"), table_name="email_tokens")
    op.drop_table("email_tokens")
    op.drop_index("uq_email_outbox_one_pending_reset", table_name="email_outbox")
    op.drop_index("ix_email_outbox_to_email_template_created", table_name="email_outbox")
    op.drop_index(
        "ix_email_outbox_pending_priority_created",
        table_name="email_outbox",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_table("email_outbox")
