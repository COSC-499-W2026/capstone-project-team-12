"""add class staff invitations

Revision ID: 726f1a45f23b
Revises: 16ab665a7245
Create Date: 2026-11-24 21:52:32.669017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "726f1a45f23b"
down_revision: str | None = "16ab665a7245"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "class_staff_invitations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("invitee_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("invited_by", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status IN ('accepted', 'declined')) = (responded_at IS NOT NULL)",
            name=op.f("ck_class_staff_invitations_responded_at_matches_status"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'cancelled', 'expired')",
            name=op.f("ck_class_staff_invitations_status"),
        ),
        sa.CheckConstraint(
            "email = lower(email)", name=op.f("ck_class_staff_invitations_email_lowercase")
        ),
        sa.ForeignKeyConstraint(
            ["class_id"], ["classes.id"], name=op.f("fk_class_staff_invitations_class_id_classes")
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"], ["users.id"], name=op.f("fk_class_staff_invitations_invited_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["invitee_id"], ["users.id"], name=op.f("fk_class_staff_invitations_invitee_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_class_staff_invitations")),
    )
    op.create_index(
        op.f("ix_class_staff_invitations_invitee_id"),
        "class_staff_invitations",
        ["invitee_id"],
        unique=False,
    )
    op.create_index(
        "uq_class_staff_invitations_one_pending",
        "class_staff_invitations",
        ["class_id", "invitee_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_class_staff_invitations_one_pending",
        table_name="class_staff_invitations",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index(
        op.f("ix_class_staff_invitations_invitee_id"), table_name="class_staff_invitations"
    )
    op.drop_table("class_staff_invitations")
