"""index due pending emails by next_attempt_at

Revision ID: 4c7e2a9b1f30
Revises: e91689d8206b
Create Date: 2026-10-14 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4c7e2a9b1f30"
down_revision: str | None = "e91689d8206b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PENDING = sa.text("status = 'pending'")


def upgrade() -> None:
    # the send job picks due rows (next_attempt_at <= now()) by priority; created_at
    # couldn't skip rows that are backing off after a failed attempt
    op.drop_index("ix_email_outbox_pending_priority_created", table_name="email_outbox")
    op.create_index(
        "ix_email_outbox_pending_priority_next_attempt",
        "email_outbox",
        ["priority", "next_attempt_at"],
        unique=False,
        postgresql_where=PENDING,
    )


def downgrade() -> None:
    op.drop_index("ix_email_outbox_pending_priority_next_attempt", table_name="email_outbox")
    op.create_index(
        "ix_email_outbox_pending_priority_created",
        "email_outbox",
        ["priority", "created_at"],
        unique=False,
        postgresql_where=PENDING,
    )
