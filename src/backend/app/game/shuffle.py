"""Shuffling a game (A9, spec Session Lifecycle, Shuffle; DFD 2.0 process 2.5).

A shuffle replaces every unfinished location of the game with a new random draw. The rows it
replaces stay, skipped with skip_reason 'shuffled', so their photo attempts stay with them; the
new rows take the same positions.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.game.control import lock_open_game, session_not_active
from app.game.draw import draw_replacements
from app.game.sessions import UNFINISHED
from app.game.start import new_location_row
from app.game.status import GameStatus, status_of
from app.models.enums import Difficulty, Language, LocationStatus, SessionState, SkipReason
from app.models.game import GameSession, SessionLocation
from app.services.audio import get_audio_service


@dataclass(frozen=True)
class ShuffledGame:
    status: GameStatus
    # the clue of the new current location
    clue_audio_url: str


def shuffle_game(student_id: UUID, game_id: UUID, shuffles_used: int, db: Session) -> ShuffledGame:
    """Replace the unfinished locations of the student's game (the current one and those not
    played yet) with new ones. Finished locations and their points don't change.

    `shuffles_used` is what the app last saw. If it no longer matches (e.g. a network retry of a
    shuffle that already happened), 409 STATE_CHANGED and nothing changes; this is checked
    before the limit, so that retry doesn't look like a used-up limit. The same 409 if the
    current location is already finished, which only happens if the game is out of step. At most
    max_shuffles_per_session (2): 409 SHUFFLE_LIMIT_REACHED. A paused game can't be shuffled:
    409 SESSION_NOT_ACTIVE (the student resumes first). NOT_ENOUGH_CONTENT if there aren't
    enough eligible locations left that the game hasn't had; the shuffle isn't used up. The same
    lookup errors as pause_game. Everything that can fail runs before the first write. A shuffle
    is a game action, so it sets last_activity_at. Flushed; doesn't commit.
    """
    game = lock_open_game(student_id, game_id, db)
    if game.state != SessionState.ACTIVE:
        raise session_not_active(game, "Resume the game before shuffling")
    if shuffles_used != game.shuffles_used:
        raise _state_changed(game)
    limit = get_settings().max_shuffles_per_session
    if game.shuffles_used >= limit:
        raise AppError(ErrorCode.SHUFFLE_LIMIT_REACHED, f"You can shuffle {limit} times a game")

    replaced = list(
        db.scalars(
            select(SessionLocation)
            .where(SessionLocation.session_id == game.id, SessionLocation.status.in_(UNFINISHED))
            .order_by(SessionLocation.position)
        )
    )
    # the current location is never finished while the game is open, so it is always among the
    # replaced rows. If it isn't, the game is out of step (e.g. a finish didn't move the
    # position on): refuse before writing anything, rather than fail with a 500.
    if game.current_position not in {row.position for row in replaced}:
        raise _state_changed(game)
    in_game = set(
        db.scalars(select(SessionLocation.location_id).where(SessionLocation.session_id == game.id))
    )
    snapshots = draw_replacements(
        student_id,
        Language(game.target_language),
        Difficulty(game.difficulty),
        in_game,
        len(replaced),
        db,
    )
    by_position = {
        row.position: snapshot for row, snapshot in zip(replaced, snapshots, strict=True)
    }
    current = by_position[game.current_position]
    clue_url = get_audio_service().clue_audio_url(current.audio_asset_id, db)

    now = datetime.now(UTC)
    for row in replaced:
        row.status = LocationStatus.SKIPPED
        row.skip_reason = SkipReason.SHUFFLED
    # one flush writes these UPDATEs before the INSERTs below, so the old rows give up their
    # positions (shuffled rows don't count in the unique index) before the new rows take them
    db.add_all(
        new_location_row(
            game.id,
            position,
            snapshot,
            current=position == game.current_position,
            now=now,
        )
        for position, snapshot in by_position.items()
    )
    game.shuffles_used += 1
    game.last_activity_at = now
    db.flush()
    return ShuffledGame(status_of(game, db), clue_url)


def _state_changed(game: GameSession) -> AppError:
    """409 STATE_CHANGED with what the app needs to try again: the current shuffles_used, state
    and position. The locations themselves come from GET /games/{id}."""
    return AppError(
        ErrorCode.STATE_CHANGED,
        "The game changed since it was loaded",
        details=[
            {
                "session_id": str(game.id),
                "state": game.state,
                "shuffles_used": game.shuffles_used,
                "current_position": game.current_position,
            }
        ],
    )
