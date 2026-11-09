"""Count weak GPS pings in a row

The GPS override is allowed after 3 minutes in Stage 1, or once the last 3 pings all had an
accuracy worse than 50 m; only the latest ping is stored, so the run of weak ones is counted.

Revision ID: 9d4b6e2a1f73
Revises: 7c3e1d9a4b52
Create Date: 2026-10-29 23:16:52.208114
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9d4b6e2a1f73"
down_revision: str | None = "7c3e1d9a4b52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "session_locations",
        sa.Column(
            "weak_pings_in_row", sa.SmallInteger(), server_default=sa.text("0"), nullable=False
        ),
    )
    op.create_check_constraint(
        op.f("ck_session_locations_weak_pings_in_row"),
        "session_locations",
        "weak_pings_in_row >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_session_locations_weak_pings_in_row"), "session_locations", type_="check"
    )
    op.drop_column("session_locations", "weak_pings_in_row")
