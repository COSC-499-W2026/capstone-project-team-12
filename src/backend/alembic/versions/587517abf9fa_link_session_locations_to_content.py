"""Link session locations to the content they snapshot

session_locations.location_id, clue_id and audio_asset_id get their foreign keys now that the
content interface and AudioService read real rows. Before that, games stored the stub's made-up
IDs, so the upgrade first checks that every row points at content that exists and stops with a
message if not: deleting those games here would erase game history.

The keys keep the default ON DELETE NO ACTION, like every other key (spec, Data Models). Content
is deactivated, never deleted, so deleting content a game used fails instead of losing history.

Revision ID: 587517abf9fa
Revises: 3b5adf42e8c1
Create Date: 2026-11-18 19:12:35.440469
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "587517abf9fa"
down_revision: str | None = "3b5adf42e8c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# column of session_locations -> the content table it points at
KEYS = {"location_id": "locations", "clue_id": "clues", "audio_asset_id": "audio_assets"}


def _name(column: str, table: str) -> str:
    return f"fk_session_locations_{column}_{table}"


def upgrade() -> None:
    connection = op.get_bind()
    for column, table in KEYS.items():
        missing = connection.scalar(
            sa.text(
                f"SELECT count(*) FROM session_locations s "
                f"WHERE NOT EXISTS (SELECT 1 FROM {table} c WHERE c.id = s.{column})"
            )
        )
        if missing:
            raise RuntimeError(
                f"{missing} session_locations rows have a {column} with no row in {table}, "
                "probably from games played on the stub content. Delete those games (or recreate "
                "the database if it only holds test data) and run the upgrade again."
            )
    for column, table in KEYS.items():
        op.create_foreign_key(
            op.f(_name(column, table)), "session_locations", table, [column], ["id"]
        )


def downgrade() -> None:
    for column, table in reversed(KEYS.items()):
        op.drop_constraint(op.f(_name(column, table)), "session_locations", type_="foreignkey")
