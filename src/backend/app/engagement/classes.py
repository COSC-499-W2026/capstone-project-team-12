"""Classroom creation operations owned by the engagement component."""

import re
import secrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import (
    ACCESS_CODE_ALPHABET,
    ACCESS_CODE_LENGTH,
    AccessCode,
    Class,
)
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

    normalized_name = name.strip()

    if not normalized_name:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Class name cannot be empty",
            details=[{"in": "body", "field": "name", "message": "Class name cannot be empty"}],
        )

    if "\x00" in normalized_name:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Class name contains a NUL character",
            details=[
                {
                    "in": "body",
                    "field": "name",
                    "message": "Class name cannot contain NUL characters",
                }
            ],
        )

    if not SEMESTER_PATTERN.fullmatch(semester):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Invalid semester format",
            details=[
                {
                    "in": "body",
                    "field": "semester",
                    "message": "Expected YYYYW1, YYYYW2, YYYYS1, or YYYYS2",
                }
            ],
        )

    school_class = Class(
        name=normalized_name,
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


def generate_access_code(
    class_id: UUID,
    creator: User,
    expires_at: datetime,
    db: Session,
    max_uses: int | None = None,
) -> AccessCode:
    """Generate an access code for a class.

    Class owners and assistants may generate codes.
    The caller owns the transaction; this function never commits.
    """
    if not creator.is_active:
        raise AppError(ErrorCode.ACCOUNT_DISABLED)

    school_class = db.get(Class, class_id)
    if school_class is None:
        raise AppError(ErrorCode.NOT_FOUND)

    role = db.execute(
        select(ClassStaff.role).where(
            ClassStaff.class_id == class_id,
            ClassStaff.user_id == creator.id,
        )
    ).scalar_one_or_none()

    if role not in (ClassStaffRole.OWNER, ClassStaffRole.ASSISTANT):
        raise AppError(ErrorCode.FORBIDDEN_ROLE)

    if school_class.archived_at is not None:
        raise AppError(ErrorCode.CLASS_ARCHIVED)

    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Expiration must include a timezone",
        )

    if expires_at <= datetime.now(UTC):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Expiration must be in the future",
        )

    if max_uses is not None and max_uses <= 0:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Maximum uses must be positive",
        )

    for _ in range(10):
        code_value = "".join(
            secrets.choice(ACCESS_CODE_ALPHABET) for _ in range(ACCESS_CODE_LENGTH)
        )

        existing = db.execute(
            select(AccessCode.id).where(AccessCode.code == code_value)
        ).scalar_one_or_none()

        if existing is not None:
            continue

        access_code = AccessCode(
            class_id=class_id,
            code=code_value,
            expires_at=expires_at,
            is_active=True,
            max_uses=max_uses,
            uses_count=0,
            created_by=creator.id,
        )

        db.add(access_code)
        db.flush()
        return access_code

    raise AppError(
        ErrorCode.INTERNAL_ERROR,
        "Unable to generate a unique access code",
    )
