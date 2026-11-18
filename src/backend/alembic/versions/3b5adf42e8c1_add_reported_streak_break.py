"""Track which streak expiration was reported."""

import sqlalchemy as sa
from alembic import op

revision = "3b5adf42e8c1"
down_revision = "28db531b4f08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_stats", sa.Column("reported_break_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_stats", "reported_break_date")
