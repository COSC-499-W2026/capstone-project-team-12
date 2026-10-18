"""Public interface of app.engagement: the only module other components may import.

Implemented:
- Issue #11: Class language, active enrolment, and archived-state lookups.
- Issue #106: Access code validation and student enrolment.
- Issue #84: Classroom and access code management.

Planned:
- Issue #15: Location result to engagement.

Callers must check class existence and archived status before enrolment
so archived classes produce CLASS_ARCHIVED rather than an enrolment error.

Engagement functions do not commit transactions. Callers are responsible
for committing or rolling back the entire operation.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.engagement.access_code_management import (
    create_access_code_record,
    save_access_code_with_retry,
)
from app.engagement.class_management import (
    create_class_record,
    list_staff_classes,
    require_class_owner,
    semester_expiration,
    update_class_record,
)
from app.errors import AppError, ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import AccessCode, Class, ClassEnrollment
from app.models.enums import (
    ClassStaffRole,
    EnrollmentEndReason,
    Language,
    UserRole,
)

__all__ = [
    "enrolment_ended_by_archive",
    "get_class_language",
    "is_actively_enrolled",
    "is_class_archived",
    "validate_code_and_enrol",
    "create_class_record",
    "list_staff_classes",
    "require_class_owner",
    "update_class_record",
    "semester_expiration",
    "save_access_code_with_retry",
    "create_access_code_record",
]


def get_class_language(class_id: UUID, db: Session) -> Language | None:
    """Return the class language, or None if the class does not exist."""
    school_class = db.get(Class, class_id)
    if school_class is None:
        return None
    return Language(school_class.target_language)


def is_actively_enrolled(student_id: UUID, class_id: UUID, db: Session) -> bool:
    """Return whether a student has an active enrolment in the class.

    Callers must check class existence and archived status first.
    """
    statement = select(ClassEnrollment.id).where(
        ClassEnrollment.student_id == student_id,
        ClassEnrollment.class_id == class_id,
        ClassEnrollment.is_active.is_(True),
    )
    return db.scalar(statement) is not None


def enrolment_ended_by_archive(student_id: UUID, class_id: UUID, db: Session) -> bool:
    """Return whether the student's enrolment in the class ended because it was archived.

    False for a student who never joined, left, was removed or is still enrolled.
    """
    statement = select(ClassEnrollment.id).where(
        ClassEnrollment.student_id == student_id,
        ClassEnrollment.class_id == class_id,
        ClassEnrollment.end_reason == EnrollmentEndReason.ARCHIVED,
    )
    return db.scalar(statement) is not None


def is_class_archived(class_id: UUID, db: Session) -> bool:
    """Return whether a class is archived.

    Returns False if the class does not exist. Check existence separately.
    """
    school_class = db.get(Class, class_id)
    return school_class is not None and school_class.archived_at is not None


def validate_code_and_enrol(
    student_id: UUID,
    code: str,
    db: Session,
) -> UUID:
    """Validate an access code and enrol a student in its class.

    Return the class ID on success.

    Raises:
        INVALID_ACCESS_CODE: Code is unknown, inactive, expired, or used up.
        ACCOUNT_DISABLED: User account is deactivated.
        FORBIDDEN_ROLE: User is missing or is not a student.
        CLASS_ARCHIVED: Class is archived.
        TA_SAME_CLASS_CONFLICT: Student assists the target class.
        ALREADY_IN_CLASS_FOR_LANGUAGE: Student already has an active class
            in the same language.
        REMOVED_FROM_CLASS: Student was removed from this class.

    Input is normalized by removing spaces and hyphens and converting
    to uppercase.

    The function does not commit. The caller owns the transaction.
    """
    normalized_code = "".join(code.upper().split()).replace("-", "")
    now = datetime.now(UTC)

    user = db.get(User, student_id)

    if user is None:
        raise AppError(ErrorCode.FORBIDDEN_ROLE)

    if not user.is_active:
        raise AppError(ErrorCode.ACCOUNT_DISABLED)

    if user.role != UserRole.STUDENT:
        raise AppError(ErrorCode.FORBIDDEN_ROLE)

    access_code = db.scalar(
        select(AccessCode).where(AccessCode.code == normalized_code).with_for_update()
    )

    if (
        access_code is None
        or not access_code.is_active
        or access_code.expires_at <= now
        or (access_code.max_uses is not None and access_code.uses_count >= access_code.max_uses)
    ):
        raise AppError(ErrorCode.INVALID_ACCESS_CODE)

    school_class = db.get(Class, access_code.class_id)
    if school_class is None:
        raise AppError(ErrorCode.INVALID_ACCESS_CODE)

    if school_class.archived_at is not None:
        raise AppError(ErrorCode.CLASS_ARCHIVED)

    assistant = db.scalar(
        select(ClassStaff.user_id).where(
            ClassStaff.user_id == student_id,
            ClassStaff.class_id == school_class.id,
            ClassStaff.role == ClassStaffRole.ASSISTANT,
        )
    )
    if assistant is not None:
        raise AppError(ErrorCode.TA_SAME_CLASS_CONFLICT)

    existing = db.scalar(
        select(ClassEnrollment).where(
            ClassEnrollment.student_id == student_id,
            ClassEnrollment.class_id == school_class.id,
        )
    )

    if existing is not None and existing.end_reason == EnrollmentEndReason.REMOVED:
        raise AppError(ErrorCode.REMOVED_FROM_CLASS)

    active_enrolment = db.scalar(
        select(ClassEnrollment.id).where(
            ClassEnrollment.student_id == student_id,
            ClassEnrollment.language == school_class.target_language,
            ClassEnrollment.is_active.is_(True),
        )
    )
    if active_enrolment is not None:
        raise AppError(ErrorCode.ALREADY_IN_CLASS_FOR_LANGUAGE)

    if existing is not None:
        # Reuse the existing row and preserve the original joining date.
        existing.is_active = True
        existing.end_reason = None
        existing.left_at = None
        existing.access_code_id = access_code.id
    else:
        db.add(
            ClassEnrollment(
                student_id=student_id,
                class_id=school_class.id,
                access_code_id=access_code.id,
                language=school_class.target_language,
                is_active=True,
                end_reason=None,
                left_at=None,
            )
        )

    # Increment atomically without committing the caller's transaction.
    db.execute(
        update(AccessCode)
        .where(AccessCode.id == access_code.id)
        .values(uses_count=AccessCode.uses_count + 1)
    )

    db.flush()
    return school_class.id
