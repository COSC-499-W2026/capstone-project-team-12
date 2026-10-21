"""Tables owned by 4.0 Class & Engagement (Fabiha).

#8 creates classes, access_codes and class_enrollments; the engagement tables follow later.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.columns import created_at, in_check, language_check, updated_at, uuid_pk
from app.models.enums import EnrollmentEndReason

# Letters and digits that are hard to mix up when read aloud or copied by hand:
# no 0/O, no 1/I/L. The code generator must pick from these characters too.
ACCESS_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
ACCESS_CODE_LENGTH = 8


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


class AccessCode(Base):
    """Codes teachers hand out; students use one to register or join a class."""

    __tablename__ = "access_codes"
    __table_args__ = (
        # stored without the hyphen, e.g. K7PM3QXD (shown to people as K7PM-3QXD)
        CheckConstraint(
            f"code ~ '^[{ACCESS_CODE_ALPHABET}]{{{ACCESS_CODE_LENGTH}}}$'", name="code_format"
        ),
        CheckConstraint("max_uses IS NULL OR max_uses > 0", name="max_uses_positive"),
        # also stops two joins at the same moment from going over the limit
        CheckConstraint(
            "uses_count >= 0 AND (max_uses IS NULL OR uses_count <= max_uses)",
            name="uses_within_max",
        ),
        # lets class_enrollments check that a code belongs to the class being joined
        UniqueConstraint("id", "class_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    class_id: Mapped[UUID] = mapped_column(ForeignKey("classes.id"), index=True)
    code: Mapped[str] = mapped_column(unique=True)
    expires_at: Mapped[datetime]  # the service defaults it to the end of the semester
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    max_uses: Mapped[int | None]  # null = unlimited
    uses_count: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()


class ClassEnrollment(Base):
    """One row per student and class, reused when the student rejoins."""

    __tablename__ = "class_enrollments"
    __table_args__ = (
        # the language is copied from the class; the composite FK keeps the two equal
        ForeignKeyConstraint(["class_id", "language"], ["classes.id", "classes.target_language"]),
        # the code used to join must be a code of this class, not of another class
        ForeignKeyConstraint(
            ["access_code_id", "class_id"], ["access_codes.id", "access_codes.class_id"]
        ),
        UniqueConstraint("student_id", "class_id"),
        language_check(),
        in_check("end_reason", EnrollmentEndReason, name="end_reason"),
        # still in the class: no end reason and no leave time; left: both are set
        CheckConstraint("is_active = (end_reason IS NULL)", name="end_reason_matches_status"),
        CheckConstraint("is_active = (left_at IS NULL)", name="left_at_matches_status"),
        CheckConstraint("left_at IS NULL OR left_at >= joined_at", name="left_after_joining"),
        # one active class per language per student
        Index(
            "uq_class_enrollments_one_active_per_language",
            "student_id",
            "language",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    student_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    class_id: Mapped[UUID] = mapped_column(index=True)  # class rosters and analytics
    access_code_id: Mapped[UUID]  # checked by the (access_code_id, class_id) FK above
    language: Mapped[str]
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    end_reason: Mapped[str | None]
    joined_at: Mapped[datetime] = created_at()
    left_at: Mapped[datetime | None]

class PointsLedger(Base):
    """Immutable record of points earned from a specific source."""

    __tablename__ = "points_ledger"
    __table_args__ = (
        UniqueConstraint("user_id", "source_type", "source_ref"),
        in_check(
            "source_type",
            ("location", "daily_challenge", "streak_milestone"),
            name="source_type",
        ),
        language_check(),
        CheckConstraint("points > 0", name="points_positive"),
    )

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    source_type: Mapped[str]
    source_ref: Mapped[str]
    class_id: Mapped[UUID | None] = mapped_column(ForeignKey("classes.id"))
    language: Mapped[str]
    points: Mapped[int]
    created_at: Mapped[datetime] = created_at()


class UserLanguageStats(Base):
    """Cumulative points earned by one student in one language."""

    __tablename__ = "user_language_stats"
    __table_args__ = (
        language_check(),
        CheckConstraint("total_points >= 0", name="total_points_nonnegative"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    language: Mapped[str] = mapped_column(primary_key=True)
    total_points: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    updated_at: Mapped[datetime] = updated_at()
