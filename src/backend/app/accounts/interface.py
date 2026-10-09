"""Public interface of app.accounts: the only module other components may import.

Planned (Interface card #10): get_current_user, require_class_role, can_manage_content and
is_ta_in_language. Agree on each signature with its users before adding it here.
"""

from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.enums import ClassStaffRole, Language, UserRole

__all__ = [
    "Language",
    "ClassStaffRole",
    "get_current_user",
    "require_class_role",
    "is_ta_in_language",
    "can_manage_content",
]

STUB_OWNER_ID = uuid4()
STUB_CONTENT_MANAGER_ID = uuid4()


class ForbiddenRoleError(Exception):
    """User does not have required authorization for action or content"""


class UserNotFoundError(Exception):
    """User could not be found."""


def get_current_user(token: str, db: Session) -> User:
    """Returns the authenticated user represented by the access token
    Stub returns a dummy user
    """
    if len(token) < 1:
        raise UserNotFoundError
    user = User()
    user.id = uuid4()
    user.email = "email@example.com"
    user.role = UserRole.STUDENT
    user.password_hash = "12345"
    user.can_manage_content = False
    user.username = "JohnLanguage"
    return user


def require_class_role(
    user: User, class_id: UUID, db: Session, *required_roles: ClassStaffRole
) -> None:
    """Raises an HTTPException if user does not have the required class roles.

    Valid required_roles are:
    - OWNER
    - ASSISTANT
    """
    if user.id != STUB_OWNER_ID:
        raise ForbiddenRoleError


def is_ta_in_language(user_id: UUID, language: Language, db: Session) -> bool:
    """Return True when the user is an assistant (TA) for at least one
    class whose target language matches `language`.
    """
    _STUB_LANGUAGE = (
        Language.ES
    )  #  For stub purposes. Any user with language set to Spanish returns true
    result = language == _STUB_LANGUAGE
    return result


def can_manage_content(user: User) -> bool:
    """Return True when the user has content management permissions in the db"""
    return user.can_manage_content
