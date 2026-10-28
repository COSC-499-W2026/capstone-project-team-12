"""Pausing and resuming a game (A8, spec Session Lifecycle).

Both count as game actions, so they set last_activity_at and the idle time starts again. A game
that has already been idle for the limit can't be paused or resumed: it is expired instead.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.game.sessions import session_idle_limit
from app.game.status import GameStatus, expire_if_idle, status_of
from app.models.enums import SessionState
from app.models.game import OPEN_SESSION_STATES, GameSession


@dataclass(frozen=True)
class PausedGame:
    status: GameStatus
    # when the game expires if nothing else happens
    expires_at: datetime


def pause_game(student_id: UUID, game_id: UUID, db: Session) -> PausedGame:
    """Pause the student's game. The current location keeps its stage and attempts.

    Pausing a paused game changes nothing. Raises 404 NOT_FOUND for another student's game, and
    409 SESSION_NOT_ACTIVE for a finished game or one that has been idle for the limit, which is
    expired first. The changes are flushed; doesn't commit. The caller commits before re-raising
    that 409, or closing the session rolls the expiry back.
    """
    game = _lock_open_game(student_id, game_id, db)
    if game.state == SessionState.ACTIVE:
        game.state = SessionState.PAUSED
        game.paused_at = game.last_activity_at = datetime.now(UTC)
        db.flush()
    return PausedGame(status_of(game, db), game.last_activity_at + session_idle_limit())


def resume_game(student_id: UUID, game_id: UUID, db: Session) -> GameStatus:
    """Resume the student's paused game where it was left.

    Resuming an active game changes nothing. The same errors as pause_game. The changes are
    flushed; doesn't commit. On the idle 409 the expiry is already in this session, so the caller
    commits before re-raising it, or closing the session rolls the expiry back.
    """
    game = _lock_open_game(student_id, game_id, db)
    if game.state == SessionState.PAUSED:
        game.state = SessionState.ACTIVE
        game.paused_at = None
        game.last_activity_at = datetime.now(UTC)
        game.resume_count += 1
        db.flush()
    return status_of(game, db)


def _lock_open_game(student_id: UUID, game_id: UUID, db: Session) -> GameSession:
    # locked and read again, so a quit, the expire job or a second pause can't change the game
    # between this check and the update. Another student's game is never locked. An idle game is
    # expired here and not committed: the caller commits before re-raising the 409.
    game = db.scalar(
        select(GameSession)
        .where(GameSession.id == game_id, GameSession.student_id == student_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if game is None:
        raise AppError(ErrorCode.NOT_FOUND, "Game not found")
    expire_if_idle(game, db)
    if game.state not in OPEN_SESSION_STATES:
        raise AppError(
            ErrorCode.SESSION_NOT_ACTIVE,
            "The game has ended",
            details=[{"session_id": str(game.id), "state": game.state}],
        )
    return game
