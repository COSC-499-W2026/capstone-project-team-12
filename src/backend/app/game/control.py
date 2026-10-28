"""Controlling a game: pause and resume (A8), quit and reset (A9). Spec Session Lifecycle;
DFD 2.0 process 2.5.

All of them count as game actions, so they set last_activity_at and the idle time starts again.
A game that has already been idle for the limit can't be played: it is expired instead.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.game.draw import draw_locations
from app.game.sessions import abandon_sessions, session_idle_limit, start_of_today
from app.game.start import StartedGame, save_new_game
from app.game.status import GameStatus, expire_if_idle, status_of
from app.models.enums import Difficulty, Language, SessionEndReason, SessionState
from app.models.game import OPEN_SESSION_STATES, GameSession
from app.services.audio import get_audio_service
from app.services.locks import lock_user


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


def quit_game(student_id: UUID, game_id: UUID, db: Session) -> GameStatus:
    """End the student's game: abandoned with end_reason quit. A paused game can be quit too.

    Points already earned stay, and the unfinished locations are skipped (session_ended), so the
    next game today in the same mode draws them first (A11). Quit is a game action, so
    last_activity_at is the quit time; that draw relies on it. The same errors as pause_game, so
    quitting twice is 409 SESSION_NOT_ACTIVE. Flushed; doesn't commit.
    """
    game = _lock_open_game(student_id, game_id, db)
    abandon_sessions(
        SessionEndReason.QUIT,
        [GameSession.id == game.id],
        db,
        last_activity_at=datetime.now(UTC),
    )
    # the UPDATE also changed the loaded game, which is locked, so it is current
    return status_of(game, db)


def reset_game(student_id: UUID, game_id: UUID, db: Session) -> StartedGame:
    """End the student's game and start a new one with the same class (or Practice), language
    and difficulty. A paused game can be reset too.

    At most max_resets_per_day (3) per local day: 409 RESET_LIMIT_REACHED. The new game is drawn
    with no carry-over, so a reset rerolls. The draw and the first clue's link come before
    anything is written, so NOT_ENOUGH_CONTENT or a missing recording leave the current game as
    it is. The old game is abandoned with end_reason reset and last_activity_at now (the daily
    limit counts it by that time); points already earned stay. The same lookup errors as
    pause_game. Flushed; doesn't commit.
    """
    # the student's lock first, then the game, the same order as starting a game, so a reset
    # and a start or a class switch wait for each other instead of deadlocking. It also stops
    # two resets from both passing the daily limit.
    lock_user(student_id, db)
    game = _lock_open_game(student_id, game_id, db)
    limit = get_settings().max_resets_per_day
    if _resets_today(student_id, db) >= limit:
        raise AppError(ErrorCode.RESET_LIMIT_REACHED, f"You can reset {limit} times a day")

    language, difficulty = Language(game.target_language), Difficulty(game.difficulty)
    snapshots = draw_locations(
        student_id, game.class_id, language, difficulty, db, carry_over=False
    )
    clue_url = get_audio_service().clue_audio_url(snapshots[0].audio_asset_id, db)

    abandon_sessions(
        SessionEndReason.RESET,
        [GameSession.id == game.id],
        db,
        last_activity_at=datetime.now(UTC),
    )
    return save_new_game(student_id, game.class_id, language, difficulty, snapshots, clue_url, db)


def _resets_today(student_id: UUID, db: Session) -> int:
    # in any mode: the limit is per student. A reset is a game action, so last_activity_at is
    # when it happened.
    count = db.scalar(
        select(func.count())
        .select_from(GameSession)
        .where(
            GameSession.student_id == student_id,
            GameSession.end_reason == SessionEndReason.RESET,
            GameSession.last_activity_at >= start_of_today(),
        )
    )
    return count or 0


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
