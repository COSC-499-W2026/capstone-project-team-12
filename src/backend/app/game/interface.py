"""Public interface of app.game: the only module other components may import.

When another component needs something from game, agree on the signature with its users first,
then add it here.

end_open_sessions: archiving a class, or a student leaving or being removed, ends their open
games. Engagement doesn't call game (that would be a cycle); the route calls engagement and then
this function, in the same transaction (spec, Architecture and Session Lifecycle).
"""

from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.enums import LocationStatus, SessionEndReason, SessionState, SkipReason
from app.models.game import OPEN_SESSION_STATES, GameSession, SessionLocation

__all__ = ["end_open_sessions"]

_UNFINISHED = (LocationStatus.PENDING, LocationStatus.NAVIGATING, LocationStatus.VERIFYING)


def end_open_sessions(class_id: UUID, db: Session, student_id: UUID | None = None) -> int:
    """End the open (active or paused) games in a class and return how many ended.

    All students' games when the class is archived, or one student's when they leave or are
    removed. The games become abandoned with end_reason class_closed, and their unfinished
    locations are skipped (session_ended). Points already earned stay, and so does
    last_activity_at, since closing a class isn't something the student did. Doesn't commit.
    """
    conditions = [GameSession.class_id == class_id, GameSession.state.in_(OPEN_SESSION_STATES)]
    if student_id is not None:
        conditions.append(GameSession.student_id == student_id)
    ended = db.scalars(
        update(GameSession)
        .where(*conditions)
        .values(
            state=SessionState.ABANDONED,
            end_reason=SessionEndReason.CLASS_CLOSED,
            paused_at=None,  # set only while paused, so it has to be cleared
        )
        .returning(GameSession.id)
    ).all()
    if ended:
        db.execute(
            update(SessionLocation)
            .where(SessionLocation.session_id.in_(ended), SessionLocation.status.in_(_UNFINISHED))
            .values(status=LocationStatus.SKIPPED, skip_reason=SkipReason.SESSION_ENDED)
        )
    return len(ended)
