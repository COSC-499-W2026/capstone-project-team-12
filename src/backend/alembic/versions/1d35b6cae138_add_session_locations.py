"""add session_locations

Revision ID: 1d35b6cae138
Revises: 57edffed62d2
Create Date: 2026-10-09 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1d35b6cae138"
down_revision: str | None = "57edffed62d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_locations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("arrived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("arrival_override", sa.Text(), nullable=True),
        sa.Column("last_lat", sa.Double(), nullable=True),
        sa.Column("last_lng", sa.Double(), nullable=True),
        sa.Column("last_accuracy_m", sa.Double(), nullable=True),
        sa.Column("last_distance_m", sa.Double(), nullable=True),
        sa.Column("last_ping_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ref_point_index", sa.Integer(), nullable=True),
        sa.Column("ref_distance_m", sa.Double(), nullable=True),
        sa.Column("ref_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_feedback_code", sa.Text(), nullable=True),
        sa.Column("last_spoken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts_used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("points_awarded", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clue_id", sa.Uuid(), nullable=False),
        sa.Column("audio_asset_id", sa.Uuid(), nullable=False),
        sa.Column(
            "accepted_answers_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("gps_points_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("radius_snapshot", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "(status = 'completed') = (points_awarded = 100)",
            name=op.f("ck_session_locations_points_match_status"),
        ),
        sa.CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name=op.f("ck_session_locations_completed_at_matches_status"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(accepted_answers_snapshot) = 'array'"
            " AND accepted_answers_snapshot <> '[]'::jsonb",
            name=op.f("ck_session_locations_accepted_answers_snapshot"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(gps_points_snapshot) = 'array' AND gps_points_snapshot <> '[]'::jsonb",
            name=op.f("ck_session_locations_gps_points_snapshot"),
        ),
        sa.CheckConstraint(
            "(status = 'skipped') = (skip_reason IS NOT NULL)",
            name=op.f("ck_session_locations_skip_reason_matches_status"),
        ),
        sa.CheckConstraint(
            "arrival_override IN ('gps_unavailable')",
            name=op.f("ck_session_locations_arrival_override"),
        ),
        sa.CheckConstraint(
            "skip_reason IN ('session_ended', 'deactivated')",
            name=op.f("ck_session_locations_skip_reason"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'navigating', 'verifying', 'completed', 'failed', 'skipped')",
            name=op.f("ck_session_locations_status"),
        ),
        sa.CheckConstraint("attempts_used >= 0", name=op.f("ck_session_locations_attempts_used")),
        sa.CheckConstraint(
            "last_accuracy_m >= 0", name=op.f("ck_session_locations_last_accuracy_m")
        ),
        sa.CheckConstraint(
            "last_lat BETWEEN -90 AND 90", name=op.f("ck_session_locations_last_lat")
        ),
        sa.CheckConstraint(
            "last_lng BETWEEN -180 AND 180", name=op.f("ck_session_locations_last_lng")
        ),
        sa.CheckConstraint(
            "points_awarded IN (0, 100)", name=op.f("ck_session_locations_points_awarded")
        ),
        sa.CheckConstraint("position >= 1", name=op.f("ck_session_locations_position")),
        sa.CheckConstraint(
            "radius_snapshot > 0", name=op.f("ck_session_locations_radius_snapshot")
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["game_sessions.id"],
            name=op.f("fk_session_locations_session_id_game_sessions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_locations")),
        sa.UniqueConstraint(
            "session_id", "location_id", name=op.f("uq_session_locations_session_id_location_id")
        ),
        sa.UniqueConstraint(
            "session_id", "position", name=op.f("uq_session_locations_session_id_position")
        ),
    )


def downgrade() -> None:
    op.drop_table("session_locations")
