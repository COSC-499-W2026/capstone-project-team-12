"""Public interface of app.engagement: the only module other components may import.

Implemented (Issue #11): class language, active enrolment, and archived-state lookups.

Planned:
- Issue #15: Location result to engagement.
- Issue #106: Access code check and enrolment.

Callers must check class existence and archived status before enrolment
so archived classes produce CLASS_ARCHIVED rather than an enrolment error.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.engagement import Class, ClassEnrollment
from app.models.enums import Language

__all__ = [
    "get_class_language",
    "is_actively_enrolled",
    "is_class_archived",
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


def is_class_archived(class_id: UUID, db: Session) -> bool:
    """Return whether a class is archived.

    Returns False if the class does not exist. Check existence separately.
    """
    school_class = db.get(Class, class_id)
    return school_class is not None and school_class.archived_at is not None
