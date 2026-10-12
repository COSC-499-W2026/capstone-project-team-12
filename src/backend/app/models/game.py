"""Tables owned by 2.0 Game Session (Mia): game_sessions, then session_locations."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.columns import created_at, in_check, language_check, uuid_pk
from app.models.enums import (
    ArrivalOverride,
    Difficulty,
    LocationStatus,
    SessionEndReason,
    SessionState,
    SkipReason,
)

# an open game can still be played; a student has at most one
OPEN_SESSION_STATES = (SessionState.ACTIVE, SessionState.PAUSED)
OPEN_STATES = "state IN ({})".format(", ".join(f"'{state}'" for state in OPEN_SESSION_STATES))


def non_empty_array(column: str) -> CheckConstraint:
    # also rejects JSON null; comparing with '[]' avoids jsonb_array_length on a non-array
    return CheckConstraint(
        f"jsonb_typeof({column}) = 'array' AND {column} <> '[]'::jsonb", name=column
    )


class GameSession(Base):
    __tablename__ = "game_sessions"
    __table_args__ = (
        # a class game uses the class language; Practice games (class_id NULL) skip this check
        ForeignKeyConstraint(
            ["class_id", "target_language"], ["classes.id", "classes.target_language"]
        ),
        language_check("target_language", name="target_language"),
        CheckConstraint(
            f"difficulty IN ({', '.join(str(d.value) for d in Difficulty)})", name="difficulty"
        ),
        in_check("state", SessionState, name="state"),
        in_check("end_reason", SessionEndReason, name="end_reason"),
        # only an abandoned game has an end reason
        CheckConstraint(
            f"(state = '{SessionState.ABANDONED}') = (end_reason IS NOT NULL)",
            name="end_reason_matches_state",
        ),
        # paused_at is set exactly while the game is paused
        CheckConstraint(
            f"(state = '{SessionState.PAUSED}') = (paused_at IS NOT NULL)",
            name="paused_at_matches_state",
        ),
        # completed_at is set exactly when all locations are finished
        CheckConstraint(
            f"(state = '{SessionState.COMPLETED}') = (completed_at IS NOT NULL)",
            name="completed_at_matches_state",
        ),
        # upper limits are settings (locations_per_game, max_shuffles_per_session),
        # so the services check those
        CheckConstraint("current_position >= 1", name="current_position"),
        CheckConstraint("shuffles_used >= 0", name="shuffles_used"),
        CheckConstraint("resume_count >= 0", name="resume_count"),
        CheckConstraint("total_score >= 0", name="total_score"),
        # a student has at most one open (active or paused) game
        Index(
            "uq_game_sessions_one_open_per_student",
            "student_id",
            unique=True,
            postgresql_where=text(OPEN_STATES),
        ),
        # the expire-sessions job looks for open games with no recent action
        Index(
            "ix_game_sessions_open_last_activity_at",
            "last_activity_at",
            postgresql_where=text(OPEN_STATES),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    student_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)  # history
    # null for Practice; indexed for archiving a class (ends its open games) and class analytics
    class_id: Mapped[UUID | None] = mapped_column(index=True)
    target_language: Mapped[str]  # copied from the class, or chosen for Practice
    difficulty: Mapped[int]
    state: Mapped[str] = mapped_column(
        default=SessionState.ACTIVE, server_default=SessionState.ACTIVE.value
    )
    end_reason: Mapped[str | None]
    current_position: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    started_at: Mapped[datetime] = created_at()
    paused_at: Mapped[datetime | None]
    last_activity_at: Mapped[datetime] = mapped_column(server_default=func.now())
    resume_count: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    shuffles_used: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    completed_at: Mapped[datetime | None]
    # sum of points_awarded, written in the same transaction as the points_ledger row
    total_score: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    created_at: Mapped[datetime] = created_at()


class SessionLocation(Base):
    """One row per location in a game, with a snapshot of its content taken when it is drawn.

    Navigation, OCR and clue playback use only the snapshot, so editing a location in the
    middle of a game doesn't change that game.
    """

    __tablename__ = "session_locations"
    __table_args__ = (
        UniqueConstraint("session_id", "position"),
        UniqueConstraint("session_id", "location_id"),  # 5 different locations per game
        # upper limits are settings (locations_per_game, max_photo_attempts); services check them
        CheckConstraint("position >= 1", name="position"),
        in_check("status", LocationStatus, name="status"),
        in_check("skip_reason", SkipReason, name="skip_reason"),
        # only a skipped location has a skip reason
        CheckConstraint(
            f"(status = '{LocationStatus.SKIPPED}') = (skip_reason IS NOT NULL)",
            name="skip_reason_matches_status",
        ),
        in_check("arrival_override", ArrivalOverride, name="arrival_override"),
        CheckConstraint("attempts_used >= 0", name="attempts_used"),
        CheckConstraint("points_awarded IN (0, 100)", name="points_awarded"),
        # a completed location earns 100 points; failed and skipped ones earn 0
        CheckConstraint(
            f"(status = '{LocationStatus.COMPLETED}') = (points_awarded = 100)",
            name="points_match_status",
        ),
        # completed_at is set exactly when the location is completed, as in game_sessions
        CheckConstraint(
            f"(status = '{LocationStatus.COMPLETED}') = (completed_at IS NOT NULL)",
            name="completed_at_matches_status",
        ),
        # a location with no answers or no entrance points could never be completed
        non_empty_array("accepted_answers_snapshot"),
        non_empty_array("gps_points_snapshot"),
        CheckConstraint("radius_snapshot > 0", name="radius_snapshot"),
        CheckConstraint("last_lat BETWEEN -90 AND 90", name="last_lat"),
        CheckConstraint("last_lng BETWEEN -180 AND 180", name="last_lng"),
        CheckConstraint("last_accuracy_m >= 0", name="last_accuracy_m"),
    )

    id: Mapped[UUID] = uuid_pk()
    session_id: Mapped[UUID] = mapped_column(ForeignKey("game_sessions.id"))
    location_id: Mapped[UUID]  # FK to locations added once Eddy's content tables exist
    position: Mapped[int]
    status: Mapped[str] = mapped_column(
        default=LocationStatus.PENDING, server_default=LocationStatus.PENDING.value
    )
    skip_reason: Mapped[str | None]
    started_at: Mapped[datetime | None]  # when it became the current location
    arrived_at: Mapped[datetime | None]  # Stage 1 confirmed
    arrival_override: Mapped[str | None]

    # latest position only (no GPS track is kept); cleared when an account is anonymised
    last_lat: Mapped[float | None]
    last_lng: Mapped[float | None]
    last_accuracy_m: Mapped[float | None]
    last_distance_m: Mapped[float | None]
    last_ping_at: Mapped[datetime | None]
    # reference point for the closer/farther trend, refreshed about every 25 s
    ref_point_index: Mapped[int | None]
    ref_distance_m: Mapped[float | None]
    ref_at: Mapped[datetime | None]
    last_feedback_code: Mapped[str | None]
    last_spoken_at: Mapped[datetime | None]

    attempts_used: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    # copy of the points_ledger row, written in the same transaction
    points_awarded: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    # e.g. ["speed_anomaly", "similar_photo", "not_in_app_capture", "gps_override"]
    # MutableList so that flags.append(...) is saved
    flags: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSONB), default=list, server_default=text("'[]'::jsonb")
    )
    completed_at: Mapped[datetime | None]

    # content snapshot taken when the location is drawn (server only, never sent to students)
    clue_id: Mapped[UUID]  # FK to clues added later
    audio_asset_id: Mapped[UUID]  # FK to audio_assets added later
    accepted_answers_snapshot: Mapped[list[str]] = mapped_column(JSONB)
    gps_points_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    radius_snapshot: Mapped[int]
