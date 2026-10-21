"""Ending games (spec Session Lifecycle): the one place that abandons a game and skips the
locations it left unfinished. Used by class closing, expiry on start and the expire job.
`get_open_session_id` uses its idle rule too."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.enums import LocationStatus, SessionEndReason, SessionState, SkipReason
from app.models.game import OPEN_SESSION_STATES, GameSession, SessionLocation

_UNFINISHED = (LocationStatus.PENDING, LocationStatus.NAVIGATING, LocationStatus.VERIFYING)


def session_idle_limit() -> timedelta:
    """How long a game may be idle before it expires (SESSION_IDLE_EXPIRY_HOURS, 24 by default)."""
    return timedelta(hours=get_settings().session_idle_expiry_hours)


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
    reason: SessionEndReason, where: Sequence[ColumnElement[bool]], db: Session
) -> list[UUID]:
    """Abandon the open games that match `where` with `reason` and skip their unfinished
    locations (session_ended). Returns the ids of the games that ended.

    Only open (active or paused) games change, in one UPDATE, so a game that ended in the
    meantime keeps its state and end reason. `where` must not be empty, so a missing filter
    can't end every game. Points already earned stay, and so does last_activity_at, since
    callers only pass system reasons (CLASS_CLOSED, EXPIRED), never something the student did.
    Doesn't commit.
    """
    if not where:
        raise ValueError("abandon_sessions needs at least one condition")
    ended = list(
        db.scalars(
            update(GameSession)
            .where(GameSession.state.in_(OPEN_SESSION_STATES), *where)
            .values(
                state=SessionState.ABANDONED,
                end_reason=reason,
                # set only while paused, so it has to be cleared
                paused_at=None,
            )
            .returning(GameSession.id)
        )
    )
    if ended:
        db.execute(
            update(SessionLocation)
            .where(SessionLocation.session_id.in_(ended), SessionLocation.status.in_(_UNFINISHED))
            .values(status=LocationStatus.SKIPPED, skip_reason=SkipReason.SESSION_ENDED)
        )
    return ended
