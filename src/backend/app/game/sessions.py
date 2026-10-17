"""Ending games (spec Session Lifecycle): the one place that abandons a game and skips the
locations it left unfinished. Used by class closing, expiry on start and the expire job."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.enums import LocationStatus, SessionEndReason, SessionState, SkipReason
from app.models.game import OPEN_SESSION_STATES, GameSession, SessionLocation

_UNFINISHED = (LocationStatus.PENDING, LocationStatus.NAVIGATING, LocationStatus.VERIFYING)


def is_idle(game: GameSession, now: datetime, idle_limit: timedelta) -> bool:
    """Whether the game has had no activity for `idle_limit` or longer, so it should expire."""
    return game.last_activity_at <= now - idle_limit


def abandon_sessions(session_ids: Sequence[UUID], reason: SessionEndReason, db: Session) -> None:
    """Abandon these games with `reason` and skip their unfinished locations (session_ended).

    Only open (active or paused) games change; one that has already ended keeps its state and
    end reason. Points already earned stay, and so does last_activity_at, since callers only pass
    system reasons (CLASS_CLOSED, EXPIRED), never something the student did. Doesn't commit.
    """
    if not session_ids:
        return
    ended = db.scalars(
        update(GameSession)
        .where(GameSession.id.in_(session_ids), GameSession.state.in_(OPEN_SESSION_STATES))
        .values(
            state=SessionState.ABANDONED,
            end_reason=reason,
            # set only while paused, so it has to be cleared
            paused_at=None,
        )
        .returning(GameSession.id)
    ).all()
    if not ended:
        return
    db.execute(
        update(SessionLocation)
        .where(SessionLocation.session_id.in_(ended), SessionLocation.status.in_(_UNFINISHED))
        .values(status=LocationStatus.SKIPPED, skip_reason=SkipReason.SESSION_ENDED)
    )
