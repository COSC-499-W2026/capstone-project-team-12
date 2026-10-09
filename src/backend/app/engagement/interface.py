"""Public interface of app.engagement: the only module other components may import.

Planned (Interface cards #11, #15 and #106): class and enrolment lookup and the class-closed
notice, the location result that returns the engagement update, and the access code check with
enrolment. Agree on each signature with its users before adding it here.

Public interface for access code validation and enrolment (Issue #106).
"""

from uuid import UUID

from sqlalchemy.orm import Session

__all__ = ["validate_code_and_enrol"]


def validate_code_and_enrol(
    student_id: UUID,
    code: str,
    db: Session,
) -> UUID:
    """Validate an access code and enrol a student in its class.

    Return the class ID on success.

    Validation failures and enrolment conflicts will be handled
    by the eventual implementation.
    """
    raise NotImplementedError
    