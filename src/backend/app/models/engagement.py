"""Tables owned by 4.0 Class & Engagement (Fabiha). #8 creates classes, the rest follow."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.columns import created_at, language_check, updated_at, uuid_pk


class Class(Base):
    __tablename__ = "classes"
    __table_args__ = (
        # composite FK target for class_enrollments and game_sessions (class_id, language)
        UniqueConstraint("id", "target_language"),
        language_check("target_language", name="target_language"),
        # UBC term codes: 2026W1 = Sep-Dec 2026, 2026W2 = Jan-Apr 2027, 2027S1 / 2027S2 = summer
        CheckConstraint("semester ~ '^[0-9]{4}(W1|W2|S1|S2)$'", name="semester_format"),
    )

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str]  # e.g. "JAPN 101 - Fall 2026"
    semester: Mapped[str]
    target_language: Mapped[str]  # chosen at creation, never changed
    leaderboard_enabled: Mapped[bool] = mapped_column(default=True, server_default=true())
    archived_at: Mapped[datetime | None]  # archived classes are read-only
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
