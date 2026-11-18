"""How a game route runs its action: one transaction, committed by the route (rule 14).

Shared by the game routes (`games.py`) and, later, the photo route (A10).
"""

from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.enums import UserRole

T = TypeVar("T")


def play(action: Callable[[], T], user: User, db: Session) -> T:
    """Run a game action and commit it.

    A 409 SESSION_NOT_ACTIVE is committed too, so an idle game expired inside the action stays
    expired. Any other refusal is rolled back, then re-raised.
    """
    if user.role == UserRole.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN_ROLE, "Admins don't play")
    try:
        result = action()
    except AppError as error:
        # an expiry is the one refusal that writes; everything else must not stick
        if error.code == ErrorCode.SESSION_NOT_ACTIVE:
            db.commit()
        else:
            db.rollback()
        raise
    db.commit()
    return result
