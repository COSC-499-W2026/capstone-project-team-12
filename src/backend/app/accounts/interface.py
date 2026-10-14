"""Public interface of app.accounts: the only module other components may import."""

import os
from collections.abc import Callable
from time import time
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import Depends, Path
from fastapi.security import OAuth2PasswordBearer
from jwt import decode
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import AppError, ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.enums import ClassStaffRole, UserRole

__all__ = [
    "ClassStaffRole",
    "get_current_user",
    "require_class_role",
    "is_class_staff",
    "can_manage_content",
]

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
ACCESS_TOKEN_KEY = os.getenv("ACCESS_TOKEN_KEY")


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)], db: Annotated[Session, Depends(get_db)]
) -> User:
    """Returns the authenticated user represented by the access token."""
    payload = decode(token, str(ACCESS_TOKEN_KEY), ["HS256"])
    user_id = payload.get("sub")
    expires_at = payload.get("exp")
    if expires_at is None:
        raise AppError(
            ErrorCode.INVALID_TOKEN,
            "Something went wrong when validating your account, please login to your account again",
        )
    if expires_at < int(time()):
        raise AppError(
            ErrorCode.INVALID_TOKEN,
            "Something went wrong when validating your account, please login to your account again",
        )
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if user is None:
        raise AppError(
            ErrorCode.INVALID_TOKEN,
            "Something went wrong when validating your account, please login to your account again",
        )
    return user


def require_class_role(*allowed_roles: ClassStaffRole) -> Callable[..., None]:
    """Raises a FORBIDDEN_ROLE error if the current user does not have one of the allowed roles."""

    def role_checker(
        user: Annotated[User, Depends(get_current_user)],
        class_id: Annotated[UUID, Path()],
        db: Annotated[Session, Depends(get_db)],
    ) -> None:
        role = db.execute(
            select(ClassStaff.role)
            .where(ClassStaff.user_id == user.id, ClassStaff.class_id == class_id)
            .limit(1)
        ).scalar_one_or_none()

        if role not in allowed_roles:
            raise AppError(ErrorCode.FORBIDDEN_ROLE)

    return role_checker


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> None:
    if not user.role == UserRole.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN_ROLE)


def is_class_staff(user_id: UUID, class_id: UUID, db: Session) -> bool:
    """Return True when the user is an assistant (TA) for the provided class`."""
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
