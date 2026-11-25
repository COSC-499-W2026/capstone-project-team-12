"""How a game route runs its action in one transaction.

The route owns the transaction and commits it; the game services it calls only flush.
"""

from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode

T = TypeVar("T")


def play(action: Callable[[], T], db: Session) -> T:
    """Run a game action and commit it.

    A 409 SESSION_NOT_ACTIVE is committed too, so an idle game expired inside the action stays
    expired. Any other refusal is rolled back, then re-raised.
    """
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
