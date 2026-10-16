"""Ending games (spec Session Lifecycle): the one place that abandons a game and skips the
locations it left unfinished. Used by class closing, expiry on start and the expire job."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.enums import LocationStatus, SessionEndReason, SessionState, SkipReason
from app.models.game import GameSession, SessionLocation

_UNFINISHED = (LocationStatus.PENDING, LocationStatus.NAVIGATING, LocationStatus.VERIFYING)


def abandon_sessions(session_ids: Sequence[UUID], reason: SessionEndReason, db: Session) -> None:
    """Abandon these games with `reason` and skip their unfinished locations (session_ended).

    Points already earned stay, and so does last_activity_at, since none of these reasons is
    something the student did. Doesn't commit.
    """
    if not session_ids:
        return
    db.execute(
        update(GameSession)
        .where(GameSession.id.in_(session_ids))
        .values(
            state=SessionState.ABANDONED,
            end_reason=reason,
            paused_at=None,  # set only while paused, so it has to be cleared
        )
    )
    db.execute(
        update(SessionLocation)
        .where(SessionLocation.session_id.in_(session_ids), SessionLocation.status.in_(_UNFINISHED))
        .values(status=LocationStatus.SKIPPED, skip_reason=SkipReason.SESSION_ENDED)
    )
