"""Public interface of app.accounts: the only module other components may import.

Planned (Interface card #10): get_current_user, require_class_role, can_manage_content and
is_ta_in_language. Agree on each signature with its users before adding it here.
"""
from sqlalchemy.orm import Session
from uuid import UUID, uuid4

from app.models.enums import UserRole, Language
from app.models.users import User

__all__ = [
    "UserRole",
    "Language",
    "get_current_user",
    "require_class_role",
    "is_ta_in_language",
    "can_manage_content"
]

def _stub_user() -> User:
    uuid = uuid4()
    user = User(uuid, "student@example.com", "student")
    return user

def get_current_user(token: str, db: Session) -> User:
    """Returns the authenticated user represented by the access token
    
    Stub returns a dummy user
    """
    return _stub_user()

def require_class_role(current_user: User, *required_roles) -> bool:
    """Returns True if the current user has one of the specified roles in the requested class.

    Valid required_roles are:
    - OWNER
    - ASSISTANT

    (Cannot type hint required_roles until class interface is agreed on)
    """
    return True

def is_ta_in_language(user_id: UUID, language: Language) -> bool:
    """Return True when the user is an assistant (TA) for at least one
    class whose target language matches `language`.

    Requires class enrolment and lookup interface as TA is a class-staff row
    """
    return True

def can_manage_content(db: Session) -> bool:
    """Return True when the user has content management permissions in the db
    """
    return True