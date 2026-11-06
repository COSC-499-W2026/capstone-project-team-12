"""Which location a game is at, and moving on to the next one (A19; DFD 2.0 processes 2.2 and 2.6).

The "next location" rule lives here only: skipping a deactivated location (A19) and finishing a
location with a photo (A10) both call `move_on`.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content import interface as content
from app.errors import AppError, ErrorCode
from app.models.enums import LocationStatus, SessionState, SkipReason
from app.models.game import GameSession, SessionLocation

_LIVE = SessionLocation.skip_reason.is_distinct_from(SkipReason.SHUFFLED)


def current_row(game: GameSession, db: Session) -> SessionLocation:
    """The game's current location row, locked until the transaction ends.

    The row at `current_position` that a shuffle didn't replace. The caller has locked the game
    first (the same order as the expire job and photo uploads). Raises 409 INVALID_STAGE if
    there is none, which only happens if the game is out of step.
    """
    row = db.execute(
        select(SessionLocation)
        .where(
            SessionLocation.session_id == game.id,
            SessionLocation.position == game.current_position,
            _LIVE,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if row is None:
        raise out_of_step(game)
    return row


def skip_if_deactivated(
    game: GameSession, row: SessionLocation, now: datetime, db: Session
) -> bool:
    """Skip the current location if a content manager deactivated it, and move on (spec,
    Content Changes During a Session). Returns whether it was skipped.

    Only a location not reached yet (pending or navigating) is skipped: one in Stage 2 lets the
    student finish the photo. Skipping counts as a game action. Doesn't commit.
    """
    if row.status not in (LocationStatus.PENDING, LocationStatus.NAVIGATING):
        return False
    if content.is_location_active(row.location_id, db):
        return False
    _skip(row)
    game.last_activity_at = now
    move_on(game, now, db)
    return True


def move_on(game: GameSession, now: datetime, db: Session) -> None:
    """Make the next live location current: navigating from `now`.

    Locations deactivated since the draw are skipped on the way. After the last location the
    game is completed; `paused_at` is cleared, since a location can finish while the game is
    paused. Engagement (A14) hooks in here once it exists: every finished location, skipped ones
    included, updates it. The caller has locked the game and finished the current location.
    Doesn't commit. A14 must not lock the user here: start/reset lock user first, else deadlock.
    """
    upcoming = db.scalars(
        select(SessionLocation)
        .where(
            SessionLocation.session_id == game.id,
            SessionLocation.position > game.current_position,
            _LIVE,
        )
        .order_by(SessionLocation.position)
        .with_for_update()
    )
    for row in upcoming:
        game.current_position = row.position
        if row.status != LocationStatus.PENDING:
            # already finished (e.g. skipped by an earlier pass); keep going
            continue
        if not content.is_location_active(row.location_id, db):
            _skip(row)
            continue
        row.status = LocationStatus.NAVIGATING
        row.started_at = now
        db.flush()
        return
    game.state = SessionState.COMPLETED
    game.completed_at = now
    game.paused_at = None
    db.flush()


def out_of_step(game: GameSession) -> AppError:
    """409 INVALID_STAGE for a game whose current location can't be played (data out of step),
    instead of failing with a 500."""
    return AppError(
        ErrorCode.INVALID_STAGE,
        "The game has no location to play at this position",
        details=[
            {
                "session_id": str(game.id),
                "state": game.state,
                "current_position": game.current_position,
            }
        ],
    )


def _skip(row: SessionLocation) -> None:
    row.status = LocationStatus.SKIPPED
    row.skip_reason = SkipReason.DEACTIVATED
