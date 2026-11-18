"""Finishing a location with a photo, the game side (A10; spec Stage 2; DFD 2.0 process 2.6).

The photo route calls `lock_photo_location` before the photo is read and `finish_photo_location`
with the result. Verification reads the photo and never imports game; game owns every change to
the game and its locations: the attempt count, the final status, scoring through
`score_finalized_location` and the next location through `move_on`.

Lock order, as everywhere: the user, then the game, then the location. Both functions lock the
user first, because scoring needs that lock and the route may call both in one transaction:
taking it only when finishing would put it after the game, the reverse of start and reset.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement.interface import EngagementResult
from app.errors import AppError, ErrorCode
from app.game.control import lock_open_game, session_not_active
from app.game.current_location import CurrentLocation, attempts_left, describe
from app.game.progress import current_row, move_on
from app.game.scoring import score_finalized_location
from app.models.enums import LocationStatus, SessionState
from app.models.game import SessionLocation
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
        raise AppError(
            ErrorCode.INVALID_STAGE,
            "The current location isn't in Stage 2",
            details=[{"session_id": str(game.id), "position": row.position, "status": row.status}],
        )
    if attempts_left(row) == 0:
        raise AppError(ErrorCode.ATTEMPTS_EXHAUSTED, "No photo attempts are left here")
    game.last_activity_at = datetime.now(UTC)
    db.flush()
    return row


def finish_photo_location(
    student_id: UUID, game_id: UUID, session_location_id: UUID, matched: bool, db: Session
) -> PhotoProgress | None:
    """Apply a photo's result to the location it was taken of (spec Stage 2: MATCH, 6th attempt).

    Every result uses an attempt. A match completes the location (100 points), and the
    max_photo_attempts-th attempt without one fails it (0 points); a finished location is scored
    and the game moves on, or is completed after its last location.

    The location is locked by id, not as the current one, because it may not be current any
    more once the photo has been read. If it is no longer in Stage 2 (a shuffle replaced it,
    A9), returns None and changes nothing. A paused game still takes the result (A8) and stays
    paused unless it is completed. The same lookup errors as lock_open_game, so a game that
    ended meanwhile is 409 SESSION_NOT_ACTIVE. A result arriving isn't something the student
    did, so last_activity_at stays. Flushed; doesn't commit.
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
    )
