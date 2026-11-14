"""Read-only classroom analytics services (A16)."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import Class
from app.models.enums import ClassStaffRole


def require_analytics_access(
    class_id: UUID,
    actor: User,
    db: Session,
) -> Class:
    """Return a classroom if the actor is its owner or an assigned TA.

    Archived classrooms remain accessible for historical statistics.
    This operation never commits, flushes, or modifies database rows.
    """
    if not actor.is_active:
        raise AppError(ErrorCode.ACCOUNT_DISABLED)

    school_class = db.get(Class, class_id)
    if school_class is None:
        raise AppError(ErrorCode.NOT_FOUND)

    role = db.scalar(
        select(ClassStaff.role).where(
            ClassStaff.class_id == class_id,
            ClassStaff.user_id == actor.id,
        )
    )

    if role not in (ClassStaffRole.OWNER, ClassStaffRole.ASSISTANT):
        raise AppError(ErrorCode.FORBIDDEN_ROLE)

    return school_class
