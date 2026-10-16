"""add locations and clues

Revision ID: b490694173b4
Revises: e91689d8206b
Create Date: 2026-10-13 15:38:44.721471
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b490694173b4"
down_revision: str | None = "e91689d8206b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "locations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("building_name", sa.Text(), nullable=False),
        sa.Column("floor_number", sa.Integer(), nullable=True),
        sa.Column("verification_type", sa.Text(), server_default="room_number", nullable=False),
        sa.Column("room_number", sa.Text(), nullable=True),
        sa.Column("accepted_answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("gps_points", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("gps_radius_meters", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(accepted_answers) = 'array' AND accepted_answers <> '[]'::jsonb",
            name=op.f("ck_locations_accepted_answers"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(gps_points) = 'array' AND gps_points <> '[]'::jsonb",
            name=op.f("ck_locations_gps_points"),
        ),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_locations_name_not_blank")),
        sa.CheckConstraint(
            "btrim(building_name) <> ''", name=op.f("ck_locations_building_name_not_blank")
        ),
        sa.CheckConstraint(
            "room_number ~ '^[A-Z]{3} [0-9]{3,4}$'", name=op.f("ck_locations_room_number_format")
        ),
        sa.CheckConstraint(
            "verification_type <> 'room_number' OR room_number IS NOT NULL",
            name=op.f("ck_locations_room_number_required"),
        ),
        sa.CheckConstraint(
            "verification_type IN ('room_number')", name=op.f("ck_locations_verification_type")
        ),
        sa.CheckConstraint("gps_radius_meters > 0", name=op.f("ck_locations_gps_radius_meters")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_locations")),
        sa.UniqueConstraint("room_number", name=op.f("uq_locations_room_number")),
    )
    op.create_table(
        "clues",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "language IN ('ja', 'zh', 'fr', 'de', 'ko', 'es')", name=op.f("ck_clues_language")
        ),
        sa.CheckConstraint("difficulty IN (1, 2, 3)", name=op.f("ck_clues_difficulty")),
        sa.ForeignKeyConstraint(
            ["location_id"], ["locations.id"], name=op.f("fk_clues_location_id_locations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clues")),
        sa.UniqueConstraint(
            "location_id",
            "difficulty",
            "language",
            name=op.f("uq_clues_location_id_difficulty_language"),
        ),
    )


def downgrade() -> None:
    op.drop_table("clues")
    op.drop_table("locations")
