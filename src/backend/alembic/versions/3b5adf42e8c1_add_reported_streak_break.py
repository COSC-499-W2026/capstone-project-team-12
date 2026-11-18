"""Track which streak expiration was reported.

Revision ID: 3b5adf42e8c1
Revises: 28db531b4f08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3b5adf42e8c1"
down_revision: str | None = "28db531b4f08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_stats", sa.Column("reported_break_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_stats", "reported_break_date")
