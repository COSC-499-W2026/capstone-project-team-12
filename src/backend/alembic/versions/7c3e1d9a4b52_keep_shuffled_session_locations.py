"""Keep session locations replaced by a shuffle

A shuffle marks the rows it replaces as skipped ('shuffled') and adds new rows at the same
positions, so the position is unique only among the rows that weren't shuffled.

Revision ID: 7c3e1d9a4b52
Revises: f4b3b8f11717
Create Date: 2026-10-27 22:46:12.512331
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c3e1d9a4b52"
down_revision: str | None = "f4b3b8f11717"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("uq_session_locations_session_id_position"), "session_locations", type_="unique"
    )
    op.create_index(
        "uq_session_locations_session_id_position",
        "session_locations",
        ["session_id", "position"],
        unique=True,
        postgresql_where=sa.text("skip_reason IS DISTINCT FROM 'shuffled'"),
    )
    op.drop_constraint(op.f("ck_session_locations_skip_reason"), "session_locations", type_="check")
    op.create_check_constraint(
        op.f("ck_session_locations_skip_reason"),
        "session_locations",
        "skip_reason IN ('session_ended', 'deactivated', 'shuffled')",
    )


def downgrade() -> None:
    # fails if any game has been shuffled: those rows break both old rules, and deleting them
    # would lose their photo attempts
    op.drop_constraint(op.f("ck_session_locations_skip_reason"), "session_locations", type_="check")
    op.create_check_constraint(
        op.f("ck_session_locations_skip_reason"),
        "session_locations",
        "skip_reason IN ('session_ended', 'deactivated')",
    )
    op.drop_index(
        "uq_session_locations_session_id_position",
        table_name="session_locations",
        postgresql_where=sa.text("skip_reason IS DISTINCT FROM 'shuffled'"),
    )
    op.create_unique_constraint(
        op.f("uq_session_locations_session_id_position"),
        "session_locations",
        ["session_id", "position"],
    )
