"""Classroom management business logic.

Public callers import these functions through app.engagement.interface.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounts import ClassStaff
from app.models.engagement import Class
from app.models.enums import ClassStaffRole


def list_staff_classes(user_id: UUID, db: Session) -> list[tuple[Class, ClassStaffRole]]:
    """Return classrooms assigned to a teacher or teaching assistant."""
    rows = db.execute(
        select(Class, ClassStaff.role)
        .join(ClassStaff, ClassStaff.class_id == Class.id)
        .where(ClassStaff.user_id == user_id)
        .order_by(Class.created_at.desc(), Class.id)
    ).all()

    return [(school_class, ClassStaffRole(role)) for school_class, role in rows]
