"""Public interface of app.accounts: the only module other components may import."""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Path
from fastapi.security import OAuth2PasswordBearer
from jwt import InvalidTokenError, decode
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.tokens import revoke_refresh_tokens
from app.config import get_settings
from app.db import get_db
from app.errors import AppError, ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import AccessCode, Class
from app.models.enums import ClassStaffRole, UserRole

__all__ = [
    "get_current_user",
    "user_id_from_token",
    "require_class_role",
    "require_code_class_role",
    "is_class_staff",
    "can_manage_content",
    "require_admin",
    "require_staff",
    "require_player",
]

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
KEY = get_settings().access_token_secret_key


def user_id_from_token(token: str) -> UUID | None:
    """The user id in a signed, unexpired access token; None for any other token."""
    try:
        payload = decode(token, str(KEY), ["HS256"], options={"require": ["exp", "sub"]})
        return UUID(payload.get("sub"))
    except (InvalidTokenError, ValueError, TypeError):
        return None


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)], db: Annotated[Session, Depends(get_db)]
) -> User:
    """Returns the authenticated user represented by the access token."""
    user_id = user_id_from_token(token)
    user = None
    if user_id is not None:
        user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if user is None:
        raise AppError(
            ErrorCode.NOT_AUTHENTICATED,
            "Something went wrong when validating your account, please login to your account again",
        )
    if not user.is_active:
        raise AppError(ErrorCode.ACCOUNT_DISABLED, "Your account has been disabled.")
    return user


def require_class_role(*allowed_roles: ClassStaffRole) -> Callable[..., ClassStaffRole]:
    """Return the allowed class role, or raise 404 or 403."""

    def role_checker(
        user: Annotated[User, Depends(get_current_user)],
        class_id: Annotated[UUID, Path()],
        db: Annotated[Session, Depends(get_db)],
    ) -> ClassStaffRole:
        if db.get(Class, class_id) is None:
            raise AppError(ErrorCode.NOT_FOUND)
        return _check_class_role(user.id, class_id, allowed_roles, db)

    return role_checker


def require_code_class_role(*allowed_roles: ClassStaffRole) -> Callable[..., None]:
    """As require_class_role, for the class of the access code in the path."""

    def role_checker(
        user: Annotated[User, Depends(get_current_user)],
        code_id: Annotated[UUID, Path()],
        db: Annotated[Session, Depends(get_db)],
    ) -> None:
        access_code = db.get(AccessCode, code_id)
        if access_code is None:
            raise AppError(ErrorCode.NOT_FOUND)
        _check_class_role(user.id, access_code.class_id, allowed_roles, db)

    return role_checker


def _check_class_role(
    user_id: UUID, class_id: UUID, allowed_roles: tuple[ClassStaffRole, ...], db: Session
) -> ClassStaffRole:
    role = db.scalar(
        select(ClassStaff.role).where(
            ClassStaff.user_id == user_id, ClassStaff.class_id == class_id
        )
    )
    if role is None or role not in allowed_roles:
        raise AppError(ErrorCode.FORBIDDEN_ROLE)
    return ClassStaffRole(role)


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> None:
    """Raises a FORBIDDEN_ROLE (403) error if current user does not have the admin role"""
    if user.role != UserRole.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN_ROLE)


def require_staff(user: Annotated[User, Depends(get_current_user)]) -> None:
    """Raises a FORBIDDEN_ROLE (403) error unless the current user is a staff (teacher) account:
    students, including TAs, and admins are refused"""
    if user.role != UserRole.STAFF:
        raise AppError(ErrorCode.FORBIDDEN_ROLE, "Only staff accounts can do this.")


def require_player(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Return the authenticated player, or raise FORBIDDEN_ROLE for an admin."""
    if user.role == UserRole.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN_ROLE, "Admins don't play")
    return user


def is_class_staff(user_id: UUID, class_id: UUID, db: Session) -> bool:
    """Return True when the user is an assistant or owner for the provided class`."""
    stmt = (
        select(ClassStaff.user_id, ClassStaff.role)
        .where(ClassStaff.user_id == user_id, ClassStaff.class_id == class_id)
        .limit(1)
    )
    result = db.execute(stmt)
    for user, role in result:
        return user == user_id and (
            role == ClassStaffRole.ASSISTANT or role == ClassStaffRole.OWNER
        )
    return False


def can_manage_content(user: Annotated[User, Depends(get_current_user)]) -> None:
    """Raises an exception when the current user does not have content management permissions"""
    if not user.can_manage_content:
        raise AppError(
            ErrorCode.FORBIDDEN_ROLE,
            "You do not have the required permissions to perform this action.",
        )


def revoke_all_refresh_tokens(user_id: UUID, db: Session) -> None:
    """Revoke every refresh token of the user. Doesn't commit.

    Lock the user row first (FOR NO KEY UPDATE, as a password reset does), in the same
    transaction. Without it, a token that a refresh or login is creating at the same time is
    missed; and a refresh locks the user before its token, so locking the tokens first could
    deadlock with it.
    """
    revoke_refresh_tokens(user_id, db)
