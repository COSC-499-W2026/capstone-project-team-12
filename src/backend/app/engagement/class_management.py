"""Classroom management business logic.

Public callers should import these functions through app.engagement.interface.
Transactions are committed by the calling API route.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models.accounts import ClassStaff
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, Language, UserRole


def semester_expiration(semester: str) -> datetime:
    """Return the exclusive UTC expiration boundary for a UBC semester."""
    year = int(semester[:4])
    term = semester[4:]

    boundaries = {
        "W1": (year + 1, 1, 1),
        "W2": (year + 1, 5, 1),
        "S1": (year, 7, 1),
        "S2": (year, 9, 1),
    }

    if term not in boundaries:
        raise ValueError("Invalid semester")

    return datetime(*boundaries[term], tzinfo=UTC)


def require_class_owner(
    *,
    class_id: UUID,
    user_id: UUID,
    db: Session,
) -> None:
    """Require an existing classroom owned by the specified user.

    Raises:
        NOT_FOUND: The classroom does not exist.
        FORBIDDEN_ROLE: The user is not the classroom owner.

    Classroom existence is checked before ownership to preserve the
    API's expected 404 response for nonexistent classrooms.
    """
    school_class = db.get(Class, class_id)

    if school_class is None:
        raise AppError(ErrorCode.NOT_FOUND)

    owner_role = db.scalar(
        select(ClassStaff.role).where(
            ClassStaff.class_id == class_id,
            ClassStaff.user_id == user_id,
        )
    )

    if owner_role != ClassStaffRole.OWNER:
        raise AppError(ErrorCode.FORBIDDEN_ROLE)


def create_class_record(
    *,
    name: str,
    semester: str,
    target_language: Language,
    creator_id: UUID,
    creator_role: UserRole,
    db: Session,
) -> Class:
    """Create a classroom and assign its creator as owner."""
    if creator_role != UserRole.STAFF:
        raise AppError(ErrorCode.FORBIDDEN_ROLE)

    if semester_expiration(semester) <= datetime.now(UTC):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Cannot create a classroom for a semester that has already ended.",
        )

    school_class = Class(
        name=name,
        semester=semester,
        target_language=target_language,
    )
    db.add(school_class)
    db.flush()

    db.add(
        ClassStaff(
            class_id=school_class.id,
            user_id=creator_id,
            role=ClassStaffRole.OWNER,
        )
    )
    db.flush()

    return school_class


def list_staff_classes(
    user_id: UUID,
    db: Session,
) -> list[tuple[Class, ClassStaffRole]]:
    """Return classrooms where the user is an owner or assistant."""
    rows = db.execute(
        select(Class, ClassStaff.role)
        .join(ClassStaff, ClassStaff.class_id == Class.id)
        .where(ClassStaff.user_id == user_id)
        .order_by(Class.created_at.desc(), Class.id)
    ).all()

    return [(school_class, ClassStaffRole(role)) for school_class, role in rows]


def update_class_record(
    *,
    class_id: UUID,
    name: str | None,
    leaderboard_enabled: bool | None,
    db: Session,
) -> Class:
    """Update permitted classroom settings.

    The API layer must authorize the classroom owner first.
    """
    school_class = db.get(Class, class_id)

    if school_class is None:
        raise AppError(ErrorCode.NOT_FOUND)

    if school_class.archived_at is not None:
        raise AppError(ErrorCode.CLASS_ARCHIVED)

    if name is not None:
        school_class.name = name

    if leaderboard_enabled is not None:
        school_class.leaderboard_enabled = leaderboard_enabled

    db.flush()
    return school_class
