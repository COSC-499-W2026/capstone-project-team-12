"""POST /games/{id}/navigation/confirm-arrival (A19, spec Stage 1: Arrival; DFD 2.0 process 2.4)."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.game.control import lock_open_game, session_not_active
from app.game.current_location import CurrentLocation, Stage, describe
from app.game.navigation import NavigationRules, is_weak, override_available_at
from app.game.progress import current_row
from app.models.enums import (
    ArrivalOverride,
    FeedbackCode,
    Language,
    LocationStatus,
    SessionState,
)
from app.models.game import GameSession, SessionLocation
from app.services.audio import get_audio_service


@dataclass(frozen=True)
class Arrival:
    location: CurrentLocation
    # the phrase to play now, VERIFY_START (the same list shape as the photo response)
    feedback: FeedbackCode
    feedback_audio_url: str


def confirm_arrival(
    student_id: UUID,
    game_id: UUID,
    override: ArrivalOverride | None,
    position: int | None,
    db: Session,
) -> Arrival:
    """Move the current location from Stage 1 to Stage 2.

    No override: 409 STALE_POSITION or NOT_WITHIN_RADIUS (`gps_weak`). The override sets
    `gps_override`, else 409 GPS_OVERRIDE_NOT_ALLOWED (3 min, or 3 weak pings). 409 STATE_CHANGED
    for a stale position, SESSION_NOT_ACTIVE, or INVALID_STAGE. Stage 2 repeats with no writes,
    even paused. Sets last_activity_at except on a repeat. Doesn't commit.
    """
    game = lock_open_game(student_id, game_id, db)
    if position is not None and position != game.current_position:
        raise _moved_on(game)
    row = current_row(game, db)
    if row.status == LocationStatus.VERIFYING:
        return _arrival(game, describe(game, row, db), db)
    if game.state != SessionState.ACTIVE:
        raise session_not_active(game, "Resume the game before confirming arrival")
    now = datetime.now(UTC)
    if override is None:
        _check_position(row, now)
    else:
        _check_override(row, now)
    # describe while still in Stage 1, so a missing clue recording fails before any write
    arrival = _arrival(game, describe(game, row, db), db)

    row.status = LocationStatus.VERIFYING
    row.arrived_at = now
    if override is not None:
        row.arrival_override = override
        # only once: a repeat returns before this
        row.flags.append("gps_override")
    game.last_activity_at = now
    db.flush()
    location = replace(arrival.location, stage=Stage.VERIFICATION, gps_override_available_at=None)
    return replace(arrival, location=location)


def _check_position(row: SessionLocation, now: datetime) -> None:
    max_age = timedelta(seconds=get_settings().arrival_ping_max_age_seconds)
    if row.last_ping_at is None or now - row.last_ping_at >= max_age:
        raise AppError(ErrorCode.STALE_POSITION, "No recent position: send a ping first")
    rules = NavigationRules.from_settings()
    if row.last_accuracy_m is not None and is_weak(row.last_accuracy_m, rules):
        raise AppError(
            ErrorCode.NOT_WITHIN_RADIUS,
            "The last position isn't at the location",
            details=[{"reason": "gps_weak"}],
        )
    if row.last_distance_m is None or row.last_distance_m > row.radius_snapshot:
        raise AppError(ErrorCode.NOT_WITHIN_RADIUS, "The last position isn't at the location")


def _check_override(row: SessionLocation, now: datetime) -> None:
    available_at = override_available_at(
        row.started_at or now,
        row.weak_pings_in_row,
        row.last_ping_at,
        NavigationRules.from_settings(),
    )
    if now < available_at:
        raise AppError(
            ErrorCode.GPS_OVERRIDE_NOT_ALLOWED,
            "Continuing without GPS isn't allowed yet",
            details=[{"gps_override_available_at": available_at.isoformat()}],
        )


def _arrival(game: GameSession, location: CurrentLocation, db: Session) -> Arrival:
    code = FeedbackCode.VERIFY_START
    language = Language(game.target_language)
    return Arrival(location, code, get_audio_service().feedback_audio_url(code, language, db))


def _moved_on(game: GameSession) -> AppError:
    """409 STATE_CHANGED: the app's position is out of date (e.g. a very late retry)."""
    return AppError(
        ErrorCode.STATE_CHANGED,
        "The game has moved on since this location",
        details=[
            {
                "session_id": str(game.id),
                "state": game.state,
                "current_position": game.current_position,
            }
        ],
    )
