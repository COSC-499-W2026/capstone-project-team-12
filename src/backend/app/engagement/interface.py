"""Public interface of app.engagement: the only module other components may import.

Planned (Interface cards #11, #15 and #106): class and enrolment lookup and the class-closed
notice, the location result that returns the engagement update, and the access code check with
enrolment. Agree on each signature with its users before adding it here.

Public interface for class and enrolment lookup (Issue #11).
"""

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.enums import Language

__all__ = [
    "get_class_language",
    "is_actively_enrolled",
    "is_class_archived",
    "notify_class_closed",
]


def get_class_language(class_id: UUID, db: Session) -> Language | None:
    """Return the class language, or None if the class does not exist."""
    raise NotImplementedError


def is_actively_enrolled(student_id: UUID, class_id: UUID, db: Session) -> bool:
    """Return whether a student has an active enrolment in the class."""
    raise NotImplementedError


def is_class_archived(class_id: UUID, db: Session) -> bool:
    """Return whether a class is archived."""
    raise NotImplementedError


def notify_class_closed(class_id: UUID, db: Session) -> None:
    """Notify open game sessions when their class is closed."""
    raise NotImplementedError
