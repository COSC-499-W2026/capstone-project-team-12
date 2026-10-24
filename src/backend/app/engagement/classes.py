"""Classroom creation operations owned by the engagement component."""

import re
import secrets
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
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

SEMESTER_END_MONTHS = {
    "W1": 12,
    "W2": 4,
    "S1": 6,
    "S2": 8,
}


def semester_end_expiration(semester: str) -> datetime:
    """Return midnight after the semester's final calendar day, in UTC."""
    match = SEMESTER_PATTERN.fullmatch(semester)
    if match is None:
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

    local_tz = ZoneInfo(get_settings().app_timezone)

    year = int(semester[:4])
    term = semester[4:]
    end_month = SEMESTER_END_MONTHS[term]

    if term == "W2":
        year += 1

    if end_month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=local_tz)
    else:
        next_month = datetime(year, end_month + 1, 1, tzinfo=local_tz)

    return next_month.astimezone(UTC)


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
    expires_at: datetime | None,
    db: Session,
    max_uses: int | None = None,
) -> AccessCode:
    """Generate an access code for a class.

    Class owners and assistants may generate codes.
    When expiration is omitted, default to the end of the class's semester.
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

    if expires_at is None:
        expires_at = semester_end_expiration(school_class.semester)
        if expires_at <= datetime.now(UTC):
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "The classroom semester has ended",
                details=[
                    {
                        "in": "body",
                        "field": "semester",
                        "message": "Cannot generate a default access code for an ended semester",
                    }
                ],
            )

    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Expiration must include a timezone",
        )

    if expires_at <= datetime.now(UTC):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Expiration must be in the future",
            details=[
                {
                    "in": "body",
                    "field": "expires_at",
                    "message": "Expiration must be in the future",
                }
            ],
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


def update_class_settings(
    class_id: UUID,
    actor: User,
    db: Session,
    *,
    name: str | None = None,
    leaderboard_enabled: bool | None = None,
) -> Class:
    """Update owner-editable classroom settings without changing its language.

    The caller owns the transaction. This function never commits.
    """
    if not actor.is_active:
        raise AppError(ErrorCode.ACCOUNT_DISABLED)

    school_class = db.get(Class, class_id)

    if school_class is None:
        raise AppError(ErrorCode.NOT_FOUND)

    role = db.execute(
        select(ClassStaff.role).where(
            ClassStaff.class_id == class_id,
            ClassStaff.user_id == actor.id,
        )
    ).scalar_one_or_none()

    if role != ClassStaffRole.OWNER:
        raise AppError(ErrorCode.FORBIDDEN_ROLE)

    if school_class.archived_at is not None:
        raise AppError(ErrorCode.CLASS_ARCHIVED)

    if name is not None:
        cleaned_name = name.strip()

        if not cleaned_name:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "Class name cannot be empty",
                details=[
                    {
                        "in": "body",
                        "field": "name",
                        "message": "Class name cannot be empty",
                    }
                ],
            )

        if "\x00" in cleaned_name:
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

        school_class.name = cleaned_name

    if leaderboard_enabled is not None:
        school_class.leaderboard_enabled = leaderboard_enabled

    db.flush()
    return school_class


def list_staff_classes(
    user_id: UUID,
    db: Session,
) -> list[tuple[Class, ClassStaffRole]]:
    """Return classes owned or assisted by the user, including archived classes.

    This operation is read-only and does not commit or flush.
    """
    statement = (
        select(Class, ClassStaff.role)
        .join(ClassStaff, ClassStaff.class_id == Class.id)
        .where(ClassStaff.user_id == user_id)
        .order_by(Class.created_at.desc(), Class.id)
    )
    return [
        (school_class, ClassStaffRole(role)) for school_class, role in db.execute(statement).all()
    ]
