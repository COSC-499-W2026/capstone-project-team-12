"""add photo attempts

Revision ID: cb2b89a8fc49
Revises: c4aead9614f3
Create Date: 2026-11-26 18:43:04.167422
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "cb2b89a8fc49"
down_revision: str | None = "c4aead9614f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "photo_attempts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_location_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("photo_path", sa.Text(), nullable=True),
        sa.Column("ocr_raw_text", sa.Text(), nullable=True),
        sa.Column("extracted_candidate", sa.Text(), nullable=True),
        sa.Column("ocr_confidence", sa.Double(), nullable=True),
        sa.Column("status", sa.Text(), server_default="processing", nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("response_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("processing_ms", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("phash", sa.BigInteger(), nullable=True),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("has_camera_exif", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'done') = (result IS NOT NULL)",
            name=op.f("ck_photo_attempts_result_matches_status"),
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_photo_attempts_content_sha256")
        ),
        sa.CheckConstraint(
            "idempotency_key ~ '^[!-~]{1,128}$'", name=op.f("ck_photo_attempts_idempotency_key")
        ),
        sa.CheckConstraint(
            "result IN ('match', 'unclear', 'no_match')", name=op.f("ck_photo_attempts_result")
        ),
        sa.CheckConstraint(
            "status IN ('processing', 'done', 'error')", name=op.f("ck_photo_attempts_status")
        ),
        sa.CheckConstraint("attempt_no >= 1", name=op.f("ck_photo_attempts_attempt_no")),
        sa.CheckConstraint(
            "ocr_confidence BETWEEN 0 AND 1", name=op.f("ck_photo_attempts_ocr_confidence")
        ),
        sa.CheckConstraint("processing_ms >= 0", name=op.f("ck_photo_attempts_processing_ms")),
        sa.ForeignKeyConstraint(
            ["session_location_id"],
            ["session_locations.id"],
            name=op.f("fk_photo_attempts_session_location_id_session_locations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_photo_attempts")),
    )
    op.create_index(
        "uq_photo_attempts_one_processing",
        "photo_attempts",
        ["session_location_id"],
        unique=True,
        postgresql_where=sa.text("status = 'processing'"),
    )
    op.create_index(
        "uq_photo_attempts_session_location_id_attempt_no",
        "photo_attempts",
        ["session_location_id", "attempt_no"],
        unique=True,
        postgresql_where=sa.text("status <> 'error'"),
    )
    op.create_index(
        "uq_photo_attempts_session_location_id_idempotency_key",
        "photo_attempts",
        ["session_location_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("status <> 'error'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_photo_attempts_session_location_id_idempotency_key",
        table_name="photo_attempts",
        postgresql_where=sa.text("status <> 'error'"),
    )
    op.drop_index(
        "uq_photo_attempts_session_location_id_attempt_no",
        table_name="photo_attempts",
        postgresql_where=sa.text("status <> 'error'"),
    )
    op.drop_index(
        "uq_photo_attempts_one_processing",
        table_name="photo_attempts",
        postgresql_where=sa.text("status = 'processing'"),
    )
    op.drop_table("photo_attempts")
