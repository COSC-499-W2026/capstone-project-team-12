"""A student's game status (A25, spec Game Sessions): the open game, or one of their games."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.game.sessions import abandon_sessions, idle_condition, is_idle, session_idle_limit
from app.models.enums import (
    Difficulty,
    Language,
    LocationStatus,
    SessionEndReason,
    SessionState,
    SkipReason,
)
from app.models.game import OPEN_SESSION_STATES, GameSession, SessionLocation


@dataclass(frozen=True)
class LocationProgress:
    """One location of a game, without anything that would give it away (no name, room or
    coordinates)."""

    position: int
    status: LocationStatus
    skip_reason: SkipReason | None
    attempts_used: int
    points_awarded: int


@dataclass(frozen=True)
class GameStatus:
    session_id: UUID
    state: SessionState
    end_reason: SessionEndReason | None
    class_id: UUID | None  # None for Practice
    language: Language
    difficulty: Difficulty
    current_position: int
    shuffles_used: int
    total_score: int
    started_at: datetime
    paused_at: datetime | None
    locations: tuple[LocationProgress, ...]


def find_current_game(student_id: UUID, db: Session) -> GameStatus | None:
    """The student's open (active or paused) game, or None.

    A game idle for the expiry time is expired first (spec: expiry is checked whenever a game is
    read), so it is never returned. The expiry is written with an UPDATE; doesn't commit.
    """
    game = db.scalar(
        select(GameSession).where(
            GameSession.student_id == student_id, GameSession.state.in_(OPEN_SESSION_STATES)
        )
    )
    if game is None:
        return None
    _expire_if_idle(game, db)
    if game.state not in OPEN_SESSION_STATES:
        return None
    return _status(game, db)


def get_game(student_id: UUID, game_id: UUID, db: Session) -> GameStatus:
    """One of the student's games, open or finished, after expiring it if it has been idle.

    Raises 404 NOT_FOUND for another student's game too, so ids can't be tried out, and that
    game is left as it is. The expiry is written with an UPDATE; doesn't commit.
    """
    game = db.get(GameSession, game_id)
    if game is None or game.student_id != student_id:
        raise AppError(ErrorCode.NOT_FOUND, "Game not found")
    _expire_if_idle(game, db)
    return _status(game, db)


def _expire_if_idle(game: GameSession, db: Session) -> None:
    now, limit = datetime.now(UTC), session_idle_limit()
    if game.state not in OPEN_SESSION_STATES or not is_idle(game, now, limit):
        return
    # the idle condition is checked again in the UPDATE, so a game another request has just
    # played isn't expired
    where = [GameSession.id == game.id, idle_condition(now, limit)]
    abandon_sessions(SessionEndReason.EXPIRED, where, db)
    # reload even if this UPDATE changed nothing: the expire job or a quit may have ended the
    # game in the meantime, and the loaded copy would still say active or paused
    db.refresh(game)


def _status(game: GameSession, db: Session) -> GameStatus:
    locations = db.scalars(
        select(SessionLocation)
        .where(SessionLocation.session_id == game.id)
        .order_by(SessionLocation.position)
        # the locations may have just been skipped by an UPDATE
        .execution_options(populate_existing=True)
    )
    return GameStatus(
        session_id=game.id,
        state=SessionState(game.state),
        end_reason=SessionEndReason(game.end_reason) if game.end_reason else None,
        class_id=game.class_id,
        language=Language(game.target_language),
        difficulty=Difficulty(game.difficulty),
        current_position=game.current_position,
        shuffles_used=game.shuffles_used,
        total_score=game.total_score,
        started_at=game.started_at,
        paused_at=game.paused_at,
        locations=tuple(
            LocationProgress(
                position=location.position,
                status=LocationStatus(location.status),
                skip_reason=SkipReason(location.skip_reason) if location.skip_reason else None,
                attempts_used=location.attempts_used,
                points_awarded=location.points_awarded,
            )
            for location in locations
        ),
    )
