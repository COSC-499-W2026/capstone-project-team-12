"""Public interface of app.game: the only module other components may import.

When another component needs something from game, agree on the signature with its users first,
then add it here.

end_open_sessions: archiving a class, or a student leaving or being removed, ends their open
games. Engagement doesn't call game (that would be a cycle); the route calls engagement and then
this function, in the same transaction (spec, Architecture and Session Lifecycle).

get_open_session_id: switching the current class or the Practice language is refused while the
student has an open game (spec, Starting a Session), so accounts asks game instead of reading
game_sessions.

expire_idle_sessions: the expire-sessions job (spec, Scheduled Jobs) ends games that have been
idle for the limit.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.game.sessions import (
    abandon_sessions,
    expire_idle_sessions,
    idle_condition,
    session_idle_limit,
)
from app.models.enums import SessionEndReason
from app.models.game import OPEN_SESSION_STATES, GameSession

__all__ = ["end_open_sessions", "expire_idle_sessions", "get_open_session_id"]


def end_open_sessions(class_id: UUID, db: Session, student_id: UUID | None = None) -> int:
    """End the open (active or paused) games in a class and return how many ended.

    All students' games when the class is archived, or one student's when they leave or are
    removed. The games become abandoned with end_reason class_closed, and their unfinished
    locations are skipped (session_ended). Points already earned stay, and so does
    last_activity_at, since closing a class isn't something the student did. Doesn't commit.
    """
    conditions = [GameSession.class_id == class_id]
    if student_id is not None:
        conditions.append(GameSession.student_id == student_id)
    return len(abandon_sessions(SessionEndReason.CLASS_CLOSED, conditions, db))


def get_open_session_id(student_id: UUID, db: Session) -> UUID | None:
    """The id of the student's open (active or paused) game, or None.

    A game that has been idle long enough to expire doesn't count, so it can't block a class
    switch. Read only.
    """
    return db.scalar(
        select(GameSession.id).where(
            GameSession.student_id == student_id,
            GameSession.state.in_(OPEN_SESSION_STATES),
            ~idle_condition(datetime.now(UTC), session_idle_limit()),
        )
    )
