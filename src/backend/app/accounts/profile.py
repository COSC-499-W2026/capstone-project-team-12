"""The student's current class and Practice language (A15, spec Student Profile)."""

from uuid import UUID

from sqlalchemy.orm import Session, object_session

from app.engagement import interface as engagement
from app.errors import AppError, ErrorCode
from app.game.interface import get_open_session_id
from app.models.accounts import User
from app.models.enums import Language
from app.services.locks import lock_user


def current_class_id(user: User, db: Session) -> UUID | None:
    """The class the student's app follows, or None for Practice.

    The stored class stays while the enrolment is active. If it is not (left, removed, archived),
    it is the most recently joined active class, or None. A stored None stays None: the student
    chose Practice, or has no class. This only reads, so a read can't overwrite a class chosen at
    the same time; the stored value is replaced the next time the student selects a class.
    """
    if user.current_class_id is None:
        return None
    active = engagement.get_active_class_ids(user.id, db)
    if user.current_class_id in active:
        return user.current_class_id
    return active[0] if active else None


def select_class(user: User, class_id: UUID | None, db: Session) -> None:
    """Make an active class of the student the current one, or None to switch to Practice.

    Raises 404 NOT_FOUND for any other class, so it doesn't show which classes exist, except 409
    CLASS_ARCHIVED for a class whose archiving ended the student's enrolment. Starting a game
    uses the same check, engagement.require_active_enrolment. Only then 409 SESSION_ALREADY_OPEN
    if the student has an open game. Choosing the class the student already sees as current
    always succeeds, even if the stored class is stale, since nothing changes for them. It is
    judged after the lock, so a change another request just committed isn't missed. The student
    must be loaded with this same db, which is how the lock reloads the values compared here and
    saves the change. Flushes, doesn't commit.
    """
    _lock(user, db)
    if class_id != current_class_id(user, db):
        if class_id is not None:
            engagement.require_active_enrolment(user.id, class_id, db)
        _refuse_if_game_open(user, db)
    # Also saves a fallback class the student chose again, since reads never write it back.
    user.current_class_id = class_id
    db.flush()


def set_practice_language(user: User, language: Language, db: Session) -> None:
    """Set the language of the student's Practice games.

    Raises 409 SESSION_ALREADY_OPEN while the student has an open game, unless the language is
    already the one set. The student must be loaded with this same db (see select_class). Flushes,
    doesn't commit.
    """
    _lock(user, db)
    if language == user.preferred_language:
        return
    _refuse_if_game_open(user, db)
    user.preferred_language = language
    db.flush()


def _lock(user: User, db: Session) -> None:
    # lock_user only reloads the object this db already holds. A student loaded by another session
    # would be compared with stale values and its change would never be saved.
    if object_session(user) is not db:
        raise ValueError("the student must be loaded with the same db session")
    lock_user(user.id, db)


def _refuse_if_game_open(user: User, db: Session) -> None:
    game_id = get_open_session_id(user.id, db)
    if game_id is not None:
        raise AppError(
            ErrorCode.SESSION_ALREADY_OPEN,
            "Finish or quit your current game first",
            details=[{"session_id": str(game_id)}],
        )
