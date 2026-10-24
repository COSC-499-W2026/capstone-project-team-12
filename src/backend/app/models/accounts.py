"""Tables owned by 1.0 Auth & Accounts (Kaedyn). #8 creates users and class_staff."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, false, func, text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.columns import created_at, in_check, language_check, updated_at, uuid_pk
from app.models.enums import ClassStaffRole, UserRole


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # the service lowercases emails; this stops a code path that forgets
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint("char_length(username) BETWEEN 3 AND 30", name="username_length"),
        CheckConstraint("username = btrim(username)", name="username_trimmed"),
        in_check("role", UserRole, name="role"),
        language_check("preferred_language", name="preferred_language"),
        # content rights are given to teacher (staff) accounts only
        CheckConstraint(
            f"NOT can_manage_content OR role = '{UserRole.STAFF}'", name="content_rights_staff_only"
        ),
        # "Mia" and "mia" count as the same username; the original case is kept for display
        Index("uq_users_username_lower", func.lower(text("username")), unique=True),
    )

    id: Mapped[UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(unique=True)  # login identifier, stored lowercase
    email_verified_at: Mapped[datetime | None]
    username: Mapped[str]  # shown on leaderboards
    # null until a staff account created by an admin sets its password from the emailed link
    password_hash: Mapped[str | None]
    role: Mapped[str]
    can_manage_content: Mapped[bool] = mapped_column(default=False, server_default=false())
    # a preference, checked on every read against the active enrolments
    current_class_id: Mapped[UUID | None] = mapped_column(ForeignKey("classes.id"))
    preferred_language: Mapped[str | None]  # Practice language
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    last_login_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class ClassStaff(Base):
    """Owner and teaching assistants of a class. Owner: a staff account; assistant: staff or
    student. Nobody is a student and a TA of the same class at once; that rule spans
    class_enrollments, so the services enforce it."""

    __tablename__ = "class_staff"
    __table_args__ = (
        in_check("role", ClassStaffRole, name="role"),
        # one owner per class (the owner is transferred, never removed); any number of TAs
        Index(
            "uq_class_staff_one_owner",
            "class_id",
            unique=True,
            # built from the enum, so renaming the value can't silently turn the rule off
            postgresql_where=text(f"role = '{ClassStaffRole.OWNER}'"),
        ),
    )

    class_id: Mapped[UUID] = mapped_column(ForeignKey("classes.id"), primary_key=True)
    # indexed: staff pages list the classes a user owns or assists
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True, index=True)
    role: Mapped[str]
    added_at: Mapped[datetime] = created_at()


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    family_id: Mapped[UUID]
    token_hash: Mapped[str]
    replaced_by: Mapped[UUID | None] = mapped_column(ForeignKey("refresh_tokens.id"))
    rotated_at: Mapped[datetime | None]
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
