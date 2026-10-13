"""Public interface of app.accounts: the only module other components may import."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.tokens import ACCESS_TOKEN_KEY, decode_jwt, revoke_refresh_tokens
from app.errors import AppError, ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.enums import ClassStaffRole

__all__ = [
    "ClassStaffRole",
    "get_current_user",
    "require_class_role",
    "is_class_staff",
    "can_manage_content",
]


async def get_current_user(token: str, db: Session) -> User:
    """Returns the authenticated user represented by the access token
    Stub returns a dummy user
    """
    payload = decode_jwt(token, str(ACCESS_TOKEN_KEY))
    user_id = payload.get("sub")
    if user_id is None:
        raise AppError(ErrorCode.INVALID_TOKEN, "")

    user = db.execute(select(User).where(User.id == user_id).limit(1)).scalar_one_or_none()
    if user is None:
        raise AppError(ErrorCode.INVALID_TOKEN)
    return user


def require_class_role(
    user: User, class_id: UUID, db: Session, required_role: ClassStaffRole
) -> None:
    """Raises if user does not have the required class roles.

    Valid required_roles are:
    - OWNER
    - ASSISTANT
    """
    try:
        stmt = (
            select(ClassStaff.role)
            .where(ClassStaff.user_id == user.id, ClassStaff.class_id == class_id)
            .limit(1)
        )
        result = db.execute(stmt)

        for role in result:
            if role[0] != required_role:
                raise AppError(
                    ErrorCode.FORBIDDEN_ROLE,
                    "You do not have the required permissions for this.",
                )
            else:
                return
    except AppError as err:
        raise AppError(err.code, err.message, err.details) from err
    except Exception as err:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            "Something went wrong when validating your permissions",
            details=[err],
        ) from err


def is_class_staff(user_id: UUID, class_id: UUID, db: Session) -> bool:
    """Return True when the user is an assistant (TA) for the provided class`."""
    try:
        stmt = (
            select(ClassStaff.user_id, ClassStaff.role)
            .where(ClassStaff.user_id == user_id)
            .limit(1)
        )
        result = db.execute(stmt)
        for user, role in result:
            return user == user_id and role == ClassStaffRole.ASSISTANT
    except Exception as err:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            "Something went wrong when validating your permissions",
            details=[err],
        ) from err
    return False


def can_manage_content(user: User) -> bool:
    """Return True when the user has content management permissions in the db"""
    return user.can_manage_content


def revoke_all_refresh_tokens(user_id: UUID, db: Session) -> None:
    revoke_refresh_tokens(user_id, db)
