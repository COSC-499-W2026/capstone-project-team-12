"""Count clue plays

How many times the student played or replayed each location's clue (#436), so teachers can see
it in class analytics.

Revision ID: c4aead9614f3
Revises: 587517abf9fa
Create Date: 2026-11-22 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4aead9614f3"
down_revision: str | None = "587517abf9fa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "session_locations",
        sa.Column("clue_plays", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_session_locations_clue_plays"), "session_locations", "clue_plays >= 0"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_session_locations_clue_plays"), "session_locations", type_="check")
    op.drop_column("session_locations", "clue_plays")
