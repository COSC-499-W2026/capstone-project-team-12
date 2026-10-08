"""Public interface of app.accounts: the only module other components may import.

Planned (Interface card #10): get_current_user, require_class_role, can_manage_content and
is_ta_in_language. Agree on each signature with its users before adding it here.
"""
from sqlalchemy.orm import Session
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.models.enums import ClassStaffRole, Language
from app.models.accounts import User

__all__ = [
    "UserRole",
    "Language",
    "get_current_user",
    "require_class_role",
    "is_ta_in_language",
    "can_manage_content"
]


def get_current_user(token: str, db: Session) -> User:
    """Returns the authenticated user represented by the access token
    
    Stub returns a dummy user
    """
    user = User()
    return User()

def require_class_role(user: User, class_id: UUID, db: Session, *required_roles: ClassStaffRole) -> None:
    """Returns True if the current user has one of the specified roles in the requested class.

    Valid required_roles are:
    - OWNER
    - ASSISTANT
    """
    raise HTTPException(status="403", detail="Unauthorized access")

def is_ta_in_language(user_id: UUID, language: Language, db: Session) -> bool:
    """Return True when the user is an assistant (TA) for at least one
    class whose target language matches `language`.
    """
    _STUB_LANGUAGE = Language.ES
    result = language == _STUB_LANGUAGE
    return result

def can_manage_content(user: User) -> bool:
    """Return True when the user has content management permissions in the db
    """
    result = user.can_manage_content
    return result