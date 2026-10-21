"""Classroom creation operations owned by the engagement component."""

import re

from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, Language, UserRole

SEMESTER_PATTERN = re.compile(r"[0-9]{4}(W1|W2|S1|S2)\Z")


def create_class(
    teacher: User,
    name: str,
    semester: str,
    language: Language,
    db: Session,
) -> Class:
    """Create a classroom and assign its teacher as the owner.

    The caller owns the transaction. This function never commits.
    """
    if not teacher.is_active:
        raise AppError(ErrorCode.ACCOUNT_DISABLED)

    if teacher.role != UserRole.STAFF:
        raise AppError(ErrorCode.FORBIDDEN_ROLE)

    if not name.strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, "Class name cannot be empty")

    if not SEMESTER_PATTERN.fullmatch(semester):
        raise AppError(ErrorCode.VALIDATION_ERROR, "Invalid semester format")

    school_class = Class(
        name=name.strip(),
        semester=semester,
        target_language=language,
    )
    db.add(school_class)
    db.flush()

    db.add(
        ClassStaff(
            class_id=school_class.id,
            user_id=teacher.id,
            role=ClassStaffRole.OWNER,
        )
    )
    db.flush()

    return school_class
