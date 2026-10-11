"""Tables owned by 2.0 Game Session (Mia): game_sessions, then session_locations."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.columns import created_at, in_check, language_check, uuid_pk
from app.models.enums import Difficulty, SessionEndReason, SessionState

OPEN_STATES = f"state IN ('{SessionState.ACTIVE}', '{SessionState.PAUSED}')"


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
