"""Classroom management business logic.

Public callers should import these functions through app.engagement.interface.
Transactions are committed by the calling API route.
"""

import re
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models.accounts import ClassStaff
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, Language, UserRole

VANCOUVER_TZ = ZoneInfo("America/Vancouver")
SEMESTER_PATTERN = re.compile(r"^([0-9]{4})(W1|W2|S1|S2)$")


def semester_expiration(semester: str) -> datetime:
    """Return the exclusive UTC expiration boundary for a UBC semester.

    Semester boundaries are defined at midnight in Vancouver local time.
    The returned datetime is converted to UTC for database comparisons.
    """
    match = SEMESTER_PATTERN.fullmatch(semester)

    if match is None:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Invalid semester format.",
        )

    year = int(match.group(1))
    term = match.group(2)

    # W1 and W2 expire in the following calendar year.
    # Years outside this range cannot produce valid expiration dates.
    if year < 1 or year > 9998:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Invalid semester year.",
        )

    boundaries = {
        "W1": (year + 1, 1, 1),
        "W2": (year + 1, 5, 1),
        "S1": (year, 7, 1),
        "S2": (year, 9, 1),
    }

    expiration_local = datetime(
        *boundaries[term],
        tzinfo=VANCOUVER_TZ,
    )

    return expiration_local.astimezone(UTC)


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
