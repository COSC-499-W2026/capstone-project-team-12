"""Finishing a location with a photo, the game side (A10; spec Stage 2; DFD 2.0 process 2.6).

Verification reads the photo and never imports game; game owns every change to the game and its
locations: the attempt count, the final status, scoring through `score_finalized_location` and the
next location through `move_on`. The photo route calls these steps around the spec's two
transactions, with OCR between them and no lock held while it runs:

1. Before OCR: `lock_photo_game` locks the game in any state, so an Idempotency-Key can be looked
   up before any state check (a replay is answered even after the game ended); then
   `lock_photo_location` locks the current location for a new upload.
2. After OCR: `lock_photographed_location` locks the location the photo was taken of, the route
   checks that the attempt still belongs to its OCR run, and `apply_photo_result` counts it.

Lock order, as everywhere: the user, then the game, then the location. Each lock step takes the
user first: `lock_photographed_location` because `apply_photo_result` scores, which needs the user
lock, and the steps before OCR so that any of them can share a transaction without reversing the
order of start and reset. `apply_photo_result` locks nothing: call it in the transaction of the
`lock_photographed_location` that returned its target.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement.interface import EngagementResult
from app.errors import AppError, ErrorCode
from app.game.control import lock_game, lock_open_game, session_not_active
from app.game.current_location import CurrentLocation, attempts_left, describe
from app.game.progress import current_row, move_on, wrong_stage
from app.game.scoring import score_finalized_location
from app.models.enums import Language, LocationStatus, SessionState
from app.models.game import GameSession, SessionLocation
from app.services.locks import lock_user


@dataclass(frozen=True)
class PhotoProgress:
    """What a photo result did to the game, for the photo response. Never an answer or a room."""

    status: LocationStatus  # verifying, completed or failed
    attempts_used: int
    attempts_left: int
    points_awarded: int
    # set once the location is finished (completed or failed)
    engagement: EngagementResult | None
    # the new current location after a finished one; None while verifying or once the game ends
    next_location: CurrentLocation | None
    game_state: SessionState
    language: Language  # the game's, for the feedback phrases


@dataclass(frozen=True)
class PhotographedLocation:
    """A location a photo was taken of and its game, both locked, for `apply_photo_result`."""

    game: GameSession
    row: SessionLocation


def lock_photo_game(student_id: UUID, game_id: UUID, db: Session) -> None:
    """Lock the student's game whatever its state, before an upload's Idempotency-Key is looked
    up (spec Stage 2: the key is checked before any status or attempt validation).

    404 NOT_FOUND for another student's game, so a key is never looked up in it. Holding the
    lock, one upload of the game at a time reaches the key lookup and the attempt reservation.
    Changes nothing: a replay isn't a game action, so an idle game is left for the game lock of
    a new upload (`lock_photo_location`) to expire.
    """
    lock_user(student_id, db)
    lock_game(student_id, game_id, db)


def lock_photo_location(student_id: UUID, game_id: UUID, db: Session) -> SessionLocation:
    """The current location, locked, before a new photo of it is read (spec Stage 2, step 2).

    404 NOT_FOUND for another student's game; 409 SESSION_NOT_ACTIVE for a finished or paused
    game, or one idle for the limit (expired here: the caller commits before re-raising); 409
    INVALID_STAGE unless the location is in Stage 2; 409 ATTEMPTS_EXHAUSTED once
    max_photo_attempts are used (only after the setting was lowered mid-game, since the last
    attempt fails the location). An upload is a game action: sets last_activity_at. Flushed;
    doesn't commit.
    """
    lock_user(student_id, db)
    game = lock_open_game(student_id, game_id, db)
    if game.state == SessionState.PAUSED:
        raise session_not_active(game, "Resume the game before sending a photo")
    row = current_row(game, db)
    if row.status != LocationStatus.VERIFYING:
        raise wrong_stage(game, row, 2)
    if attempts_left(row) == 0:
        raise AppError(ErrorCode.ATTEMPTS_EXHAUSTED, "No photo attempts are left here")
    game.last_activity_at = datetime.now(UTC)
    db.flush()
    return row


def lock_photographed_location(
    student_id: UUID, game_id: UUID, session_location_id: UUID, db: Session
) -> PhotographedLocation | None:
    """The location a photo was taken of, locked with its game, if it can still take the
    photo's result.

    Locked by id, not as the current one, because it may not be current any more once the
    photo has been read. None if it is no longer in Stage 2 (a shuffle replaced it, A9, or a
    match or the last attempt finished it); the result is dropped then. A location still in
    Stage 2 can't tell whether this photo's result was already counted: the caller checks that
    the attempt still belongs to its OCR run first. A paused game still takes the result (A8).
    The same lookup errors as lock_open_game, so a game that ended meanwhile is 409
    SESSION_NOT_ACTIVE, and one idle for the limit is expired (the caller commits before
    re-raising). Otherwise changes nothing.
    """
    lock_user(student_id, db)
    game = lock_open_game(student_id, game_id, db)
    row = db.scalar(
        select(SessionLocation)
        .where(SessionLocation.id == session_location_id, SessionLocation.session_id == game.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.status != LocationStatus.VERIFYING:
        return None
    return PhotographedLocation(game, row)


def apply_photo_result(target: PhotographedLocation, matched: bool, db: Session) -> PhotoProgress:
    """Count a photo's result at the location from `lock_photographed_location`, in the same
    transaction, so the rows are still locked (spec Stage 2: MATCH, 6th attempt).

    Every result uses an attempt. A match completes the location (100 points), and the
    max_photo_attempts-th attempt without one fails it (0 points); a finished location is scored
    and the game moves on, or is completed after its last location. A paused game stays paused
    unless it is completed. A result arriving isn't something the student did, so
    last_activity_at stays. Flushed; doesn't commit.

    ValueError for a location that is no longer in Stage 2 (a result applied to it twice).
    """
    game, row = target.game, target.row
    if row.status != LocationStatus.VERIFYING:
        raise ValueError("Only a location in Stage 2 takes a photo result")
    now = datetime.now(UTC)
    row.attempts_used += 1
    if matched:
        row.status = LocationStatus.COMPLETED
        row.completed_at = now
    elif attempts_left(row) == 0:
        row.status = LocationStatus.FAILED
    engagement = upcoming = None
    if row.status != LocationStatus.VERIFYING:
        engagement = score_finalized_location(game, row, db)
        move_on(game, now, db)
        if game.state != SessionState.COMPLETED:
            upcoming = describe(game, current_row(game, db), db)
    db.flush()
    return PhotoProgress(
        status=LocationStatus(row.status),
        attempts_used=row.attempts_used,
        attempts_left=attempts_left(row),
        points_awarded=row.points_awarded,
        engagement=engagement,
        next_location=upcoming,
        game_state=SessionState(game.state),
        language=Language(game.target_language),
    )
