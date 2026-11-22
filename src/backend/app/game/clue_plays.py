"""POST /games/{id}/clue-plays (#436): count each play or replay of the current location's clue."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.game.control import lock_open_game, session_not_active
from app.game.progress import current_row, moved_on
from app.models.enums import SessionState


def record_clue_play(student_id: UUID, game_id: UUID, position: int, db: Session) -> int:
    """Add one play to the current location and return its count.

    404 NOT_FOUND for another student's game, 409 SESSION_NOT_ACTIVE unless the game is active,
    409 STATE_CHANGED if `position` isn't the current one; nothing is written then. A play is a
    game action, so it sets last_activity_at. Flushed; doesn't commit.
    """
    game = lock_open_game(student_id, game_id, db)
    if game.state != SessionState.ACTIVE:
        raise session_not_active(game, "Resume the game before playing the clue")
    if position != game.current_position:
        raise moved_on(game)
    row = current_row(game, db)
    row.clue_plays += 1
    game.last_activity_at = datetime.now(UTC)
    db.flush()
    return row.clue_plays
