"""Check that a game has ended_at exactly when it has ended

Revision ID: 4da36b03f268
Revises: afba2e151aa3
Create Date: 2026-11-25 16:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "4da36b03f268"
down_revision: str | None = "afba2e151aa3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE game_sessions SET ended_at = COALESCE(completed_at, last_activity_at) "
        "WHERE state IN ('completed', 'abandoned') AND ended_at IS NULL"
    )
    op.create_check_constraint(
        op.f("ck_game_sessions_ended_at_matches_state"),
        "game_sessions",
        "(state IN ('completed', 'abandoned')) = (ended_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_game_sessions_ended_at_matches_state"), "game_sessions", type_="check"
    )
