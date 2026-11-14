"""A game's summary (A23, #47): how each location went, the points, and what the game added to
the student's level and badges.

summary_of builds it for a loaded game, so a finishing response (quit, a game completed on its
last location, A10's last photo) can return it in the same transaction.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement import interface as engagement
from app.game.status import GameStatus, load_game, status_of
from app.models.enums import Language, SessionState
from app.models.game import GameSession, SessionLocation


@dataclass(frozen=True)
class GameSummary:
    status: GameStatus
    ended_at: datetime | None  # None while the game is open
    engagement: engagement.GameEngagement  # level, level_up and new badges, as of this game


def get_summary(student_id: UUID, game_id: UUID, db: Session) -> GameSummary:
    """The summary of one of the student's games, open or finished; an open game shows its
    results so far. Expiry and 404 as for its status (get_game). Doesn't commit."""
    return summary_of(load_game(student_id, game_id, db), db)


def summary_of(game: GameSession, db: Session) -> GameSummary:
    """The summary of a loaded game, read fresh from the database. Doesn't write."""
    # every row, shuffled ones too: only finished rows have awards, and a shuffle never replaces one
    location_ids = db.scalars(
        select(SessionLocation.id).where(SessionLocation.session_id == game.id)
    ).all()
    return GameSummary(
        status=status_of(game, db),
        ended_at=_ended_at(game),
        engagement=engagement.get_game_engagement(
            game.student_id,
            Language(game.target_language),
            location_ids,
            game.started_at,
            db,
        ),
    )


def _ended_at(game: GameSession) -> datetime | None:
    """When the game ended: completed_at for a completed game. An abandoned one has no end
    column; quit and reset set last_activity_at to the end time, and an expired or class-closed
    game keeps its last action there."""
    if game.state == SessionState.COMPLETED:
        return game.completed_at
    if game.state == SessionState.ABANDONED:
        return game.last_activity_at
    return None
