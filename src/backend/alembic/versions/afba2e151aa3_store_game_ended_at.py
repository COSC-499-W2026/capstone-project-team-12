"""Store when a game ended

ended_at (#348): when the game was completed, quit, reset, expired or closed with its class.
Before this, the history and the summary used last_activity_at for every abandoned game, which
for an expired or class-closed game is the student's last action, not the end. Games that
already ended get completed_at, or last_activity_at, the best value there is for them.

Revision ID: afba2e151aa3
Revises: c4aead9614f3
Create Date: 2026-11-25 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "afba2e151aa3"
down_revision: str | None = "c4aead9614f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("game_sessions", sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE game_sessions SET ended_at = COALESCE(completed_at, last_activity_at) "
        "WHERE state IN ('completed', 'abandoned')"
    )


def downgrade() -> None:
    op.drop_column("game_sessions", "ended_at")
