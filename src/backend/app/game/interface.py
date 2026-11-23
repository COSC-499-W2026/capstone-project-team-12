"""Public interface of app.game: the only module other components may import.

When another component needs something from game, agree on the signature with its users first,
then add it here.

end_open_sessions: archiving a class, or a student leaving or being removed, ends their open
games. Engagement doesn't call game (that would be a cycle); the route calls engagement and then
this function, in the same transaction (spec, Architecture and Session Lifecycle).

get_open_session_id: switching the current class or the Practice language is refused while the
student has an open game (spec, Starting a Session), so accounts asks game instead of reading
game_sessions. refuse_if_game_open raises the same 409 as starting a game.

expire_idle_sessions: the expire-sessions job (spec, Scheduled Jobs) ends games that have been
idle for the limit.

list_history: GET /me/games reads one page of the student's finished games.

list_games_for_export: an admin's export of an account holds every game the student played,
with its locations.

clear_positions: deleting (anonymising) an account clears the GPS positions kept in its games.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.game.export import list_games_for_export
from app.game.history import list_history
from app.game.sessions import (
    abandon_sessions,
    expire_idle_sessions,
    game_already_open,
    idle_condition,
    session_idle_limit,
)
from app.models.enums import SessionEndReason
from app.models.game import OPEN_SESSION_STATES, GameSession, SessionLocation

__all__ = [
    "clear_positions",
    "end_open_sessions",
    "expire_idle_sessions",
    "get_open_session_id",
    "list_games_for_export",
    "list_history",
    "refuse_if_game_open",
]


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


def refuse_if_game_open(student_id: UUID, db: Session) -> None:
    """Raise 409 SESSION_ALREADY_OPEN (the open game's id in details) if the student has an open
    game, as get_open_session_id counts it. Read only."""
    game_id = get_open_session_id(student_id, db)
    if game_id is not None:
        raise game_already_open(game_id)


def clear_positions(student_id: UUID, db: Session) -> None:
    """Forget every GPS position the student sent, in all their games: the last position at each
    location and the distances worked out from it. Results, points and flags stay. Doesn't
    commit."""
    games = select(GameSession.id).where(GameSession.student_id == student_id)
    db.execute(
        update(SessionLocation)
        .where(SessionLocation.session_id.in_(games))
        .values(
            last_lat=None,
            last_lng=None,
            last_accuracy_m=None,
            last_distance_m=None,
            last_ping_at=None,
            ref_point_index=None,
            ref_distance_m=None,
            ref_at=None,
        )
    )
