"""Tables owned by 3.0 Verification (Eddy): photo_attempts, one row per photo upload (D6).

An upload reserves its attempt as `processing` and commits before OCR starts; the OCR run then
sets the row `done` with its result, or `error` (spec, Stage 2: Verification, "Retries and
concurrency"). Two rules here are proposed, not in the spec (A10 plan Q3, team to confirm):

- An `error` row has no `attempt_no`, so a failed or timed-out run releases its number and the
  retry can take it without breaking `UNIQUE (session_location_id, attempt_no)`.
- At most one `processing` row per session location, so one location's photos are read one at a
  time and two uploads can never both use the last attempt.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.columns import created_at, in_check, uuid_pk
from app.models.enums import PhotoAttemptStatus, PhotoResult


class PhotoAttempt(Base):
    """One photo upload for one session location row, its OCR result and its stored response.

    A row replaced by a shuffle keeps its attempts, and the new row starts again at attempt 1.
    """

    __tablename__ = "photo_attempts"
    __table_args__ = (
        # attempt numbers count per session location row; NULL (an error row) never collides
        UniqueConstraint("session_location_id", "attempt_no"),
        # a key is valid for one session location (spec, "Key scope"); uploads without one are
        # always new attempts, and NULLs never collide
        UniqueConstraint("session_location_id", "idempotency_key"),
        Index(
            "uq_photo_attempts_one_processing",
            "session_location_id",
            unique=True,
            postgresql_where=text(f"status = '{PhotoAttemptStatus.PROCESSING}'"),
        ),
        in_check("status", PhotoAttemptStatus, name="status"),
        in_check("result", PhotoResult, name="result"),
        # a result exists exactly when the run finished
        CheckConstraint(
            f"(status = '{PhotoAttemptStatus.DONE}') = (result IS NOT NULL)",
            name="result_matches_status",
        ),
        # an error row has released its attempt number
        CheckConstraint(
            f"(status = '{PhotoAttemptStatus.ERROR}') = (attempt_no IS NULL)",
            name="attempt_no_matches_status",
        ),
        # the upper limit is a setting (max_photo_attempts), so the services check it
        CheckConstraint("attempt_no >= 1", name="attempt_no"),
        CheckConstraint("ocr_confidence BETWEEN 0 AND 1", name="ocr_confidence"),
        CheckConstraint("processing_ms >= 0", name="processing_ms"),
        CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha256"),
    )

    id: Mapped[UUID] = uuid_pk()
    session_location_id: Mapped[UUID] = mapped_column(ForeignKey("session_locations.id"))
    attempt_no: Mapped[int | None]
    # random storage key of the re-encoded photo; cleared when the photo is purged
    photo_path: Mapped[str | None]
    # OCR output stays on the server: it may contain the room number, so never send it to students
    ocr_raw_text: Mapped[str | None]
    extracted_candidate: Mapped[str | None]  # normalised, e.g. SCI333
    ocr_confidence: Mapped[float | None]
    status: Mapped[str] = mapped_column(
        default=PhotoAttemptStatus.PROCESSING, server_default=PhotoAttemptStatus.PROCESSING.value
    )
    result: Mapped[str | None]
    # full API response, replayed for a repeated Idempotency-Key; cleared 24 h after the location
    # finishes, so a done row may have none
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    processing_ms: Mapped[int | None]
    idempotency_key: Mapped[str | None]
    # 64-bit perceptual hash for the similar-photo flag, stored signed; cleared with the photo
    phash: Mapped[int | None] = mapped_column(BigInteger)
    # SHA-256 of the uploaded file, so the same key with another file is detected
    content_sha256: Mapped[str]
    # the OCR run that owns the row; a run only writes its result while the row still has its id
    job_id: Mapped[UUID]
    # in-app camera frames never carry camera EXIF (images.prepare_photo decides)
    has_camera_exif: Mapped[bool]
    created_at: Mapped[datetime] = created_at()
