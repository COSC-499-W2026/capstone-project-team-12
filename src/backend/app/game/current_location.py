"""GET /games/{id}/current-location (A19, spec API table; DFD 2.0 process 2.2).

What the app needs to play the current location: its position and stage, the clue link, the
attempts and when the GPS override is allowed. Never a name, room number, coordinates or answer.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.game.control import lock_open_game
from app.game.navigation import NavigationRules, override_available_at
from app.game.progress import current_row, out_of_step, skip_if_deactivated
from app.game.status import GameStatus, status_of
from app.models.enums import LocationStatus, SessionState
from app.models.game import GameSession, SessionLocation
from app.services.audio import get_audio_service


class Stage(StrEnum):
    NAVIGATION = "navigation"  # Stage 1
    VERIFICATION = "verification"  # Stage 2


_STAGES = {
    LocationStatus.NAVIGATING: Stage.NAVIGATION,
    LocationStatus.VERIFYING: Stage.VERIFICATION,
}


@dataclass(frozen=True)
class CurrentLocation:
    session_id: UUID
    state: SessionState
    position: int
    stage: Stage
    clue_audio_url: str
    attempts_used: int
    attempts_left: int
    # Stage 1 only: compare with the current time, don't count down from it (it can move)
    gps_override_available_at: datetime | None


def get_current_location(
    student_id: UUID, game_id: UUID, db: Session
) -> CurrentLocation | GameStatus:
    """The student's current location. A paused game can be read too.

    If the location was deactivated before the student reached it, it is skipped (0 points) and
    the next one is returned; if it was the last one, the game is completed and its status comes
    back instead (the summary once it exists, #47). The same lookup errors as pause_game, and 409
    INVALID_STAGE if the game is out of step. Doesn't commit; a skip is flushed.
    """
    game = lock_open_game(student_id, game_id, db)
    row = current_row(game, db)
    if skip_if_deactivated(game, row, datetime.now(UTC), db):
        if game.state == SessionState.COMPLETED:
            return status_of(game, db)
        row = current_row(game, db)
    return describe(game, row, db)


def describe(game: GameSession, row: SessionLocation, db: Session) -> CurrentLocation:
    """The current-location body for a game and its current row."""
    stage = _STAGES.get(LocationStatus(row.status))
    if stage is None:
        raise out_of_step(game)
    override_at = None
    if stage == Stage.NAVIGATION:
        override_at = override_available_at(
            row.started_at or datetime.now(UTC),
            0,  # weak pings in a row: counted from the ping PR on
            row.last_ping_at,
            NavigationRules.from_settings(),
        )
    return CurrentLocation(
        session_id=game.id,
        state=SessionState(game.state),
        position=row.position,
        stage=stage,
        clue_audio_url=get_audio_service().clue_audio_url(row.audio_asset_id, db),
        attempts_used=row.attempts_used,
        attempts_left=max(0, get_settings().max_photo_attempts - row.attempts_used),
        gps_override_available_at=override_at,
    )
