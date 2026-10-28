"""Ending games (spec Session Lifecycle): the one place that abandons a game and skips the
locations it left unfinished. Used by quit, class closing, expiry on start, expiry on read and
the expire job. `get_open_session_id` uses its idle rule too."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.models.enums import LocationStatus, SessionEndReason, SessionState, SkipReason
from app.models.game import OPEN_SESSION_STATES, GameSession, SessionLocation

# not played to the end yet: what ending a game skips and what a shuffle replaces
UNFINISHED = (LocationStatus.PENDING, LocationStatus.NAVIGATING, LocationStatus.VERIFYING)


def session_idle_limit() -> timedelta:
    """How long a game may be idle before it expires (SESSION_IDLE_EXPIRY_HOURS, 24 by default)."""
    return timedelta(hours=get_settings().session_idle_expiry_hours)


def start_of_today() -> datetime:
    """Midnight at the start of today in the app's time zone (America/Vancouver by default).

    The day-based game rules (carry-over after a quit, the daily reset limit) use the local
    calendar day, so they turn over at local midnight, also across daylight saving changes.
    """
    now = datetime.now(ZoneInfo(get_settings().app_timezone))
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def game_already_open(game_id: UUID) -> AppError:
    """The 409 SESSION_ALREADY_OPEN for a student who has an open game, with its id in details."""
    return AppError(
        ErrorCode.SESSION_ALREADY_OPEN,
        "Finish or quit your current game first",
        details=[{"session_id": str(game_id)}],
    )


def idle_cutoff(now: datetime, idle_limit: timedelta) -> datetime:
    """A game whose last activity is at or before this time has been idle for the limit."""
    return now - idle_limit


def is_idle(game: GameSession, now: datetime, idle_limit: timedelta) -> bool:
    """Whether one loaded game has had no activity for `idle_limit` or longer (it should expire)."""
    return game.last_activity_at <= idle_cutoff(now, idle_limit)


def idle_condition(now: datetime, idle_limit: timedelta) -> ColumnElement[bool]:
    """`is_idle` as a WHERE condition, for choosing the games to expire in one query."""
    return GameSession.last_activity_at <= idle_cutoff(now, idle_limit)


def abandon_sessions(
    reason: SessionEndReason,
    where: Sequence[ColumnElement[bool]],
    db: Session,
    *,
    last_activity_at: datetime | None = None,
) -> list[UUID]:
    """Abandon the open games that match `where` with `reason` and skip their unfinished
    locations (session_ended). Returns the ids of the games that ended.

    Only open (active or paused) games change, in one UPDATE, so a game that ended in the
    meantime keeps its state and end reason. `where` must not be empty, so a missing filter
    can't end every game. Points already earned stay.

    `last_activity_at` is for an end the student asked for (quit, reset): it is a game action, so
    it becomes the last activity. The system reasons (CLASS_CLOSED, EXPIRED) leave it out and keep
    the old value, since the student did nothing. Doesn't commit.
    """
    if not where:
        raise ValueError("abandon_sessions needs at least one condition")
    values: dict[str, object] = {
        "state": SessionState.ABANDONED,
        "end_reason": reason,
        # set only while paused, so it has to be cleared
        "paused_at": None,
    }
    if last_activity_at is not None:
        values["last_activity_at"] = last_activity_at
    ended = list(
        db.scalars(
            update(GameSession)
            .where(GameSession.state.in_(OPEN_SESSION_STATES), *where)
            .values(values)
            .returning(GameSession.id)
        )
    )
    if ended:
        # locations after the game: a photo upload (A10) must lock the game first, or they deadlock
        db.execute(
            update(SessionLocation)
            .where(SessionLocation.session_id.in_(ended), SessionLocation.status.in_(UNFINISHED))
            .values(status=LocationStatus.SKIPPED, skip_reason=SkipReason.SESSION_ENDED)
        )
    return ended


def expire_idle_sessions(db: Session, *, batch_size: int) -> int:
    """Expire up to `batch_size` open games that have been idle for the limit, and return how many
    ended (the expire-sessions job runs it until a batch comes back short).

    `batch_size` must be positive. The games are locked in id order with SKIP LOCKED, so a game
    another request is changing (a pause, a start that expires it) is left for the next run
    instead of waiting on it, and two runs can't deadlock. Doesn't commit.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    now, limit = datetime.now(UTC), session_idle_limit()
    batch = list(
        db.scalars(
            select(GameSession.id)
            .where(GameSession.state.in_(OPEN_SESSION_STATES), idle_condition(now, limit))
            .order_by(GameSession.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    if not batch:
        return 0
    return len(abandon_sessions(SessionEndReason.EXPIRED, [GameSession.id.in_(batch)], db))
