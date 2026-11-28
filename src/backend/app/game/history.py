"""A student's game history (A23, #47): their finished games, newest first, a page at a time.

A finished game is completed or abandoned (quit, reset, expired or class closed). Open games are
not history; the open game is read with GET /games/current.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.game.status import expire_open_game
from app.models.enums import (
    Difficulty,
    Language,
    LocationStatus,
    SessionEndReason,
    SessionState,
)
from app.models.game import GameSession, SessionLocation

FINISHED_STATES = (SessionState.COMPLETED, SessionState.ABANDONED)


@dataclass(frozen=True)
class HistoryItem:
    """One finished game. No location names, rooms or coordinates."""

    session_id: UUID
    started_at: datetime
    ended_at: datetime
    language: Language
    difficulty: Difficulty
    class_id: UUID | None  # None for Practice
    state: SessionState
    end_reason: SessionEndReason | None  # None for a completed game
    total_score: int
    locations_completed: int


@dataclass(frozen=True)
class HistoryPage:
    items: tuple[HistoryItem, ...]
    total: int  # finished games matching the filter, on every page


def list_history(
    student_id: UUID,
    language: Language | None,
    page: int,
    page_size: int,
    db: Session,
) -> HistoryPage:
    """The student's finished games, newest end first (then by id), optionally in one language.

    The student's open game is expired first if it has been idle for the limit (spec: expiry is
    checked whenever a game is read), so it shows up here at once. That is an UPDATE; doesn't
    commit. A page past the end has no items.
    """
    expire_open_game(student_id, db)
    finished = [GameSession.student_id == student_id, GameSession.state.in_(FINISHED_STATES)]
    if language is not None:
        finished.append(GameSession.target_language == language)

    total = db.scalar(select(func.count()).select_from(GameSession).where(*finished)) or 0
    if (page - 1) * page_size >= total:
        return HistoryPage(items=(), total=total)
    # a shuffled-away row is skipped, never completed, so it isn't counted
    completed = (
        select(func.count())
        .select_from(SessionLocation)
        .where(
            SessionLocation.session_id == GameSession.id,
            SessionLocation.status == LocationStatus.COMPLETED,
        )
        .correlate(GameSession)
        .scalar_subquery()
    )
    rows = db.execute(
        select(GameSession, completed)
        .where(*finished)
        .order_by(GameSession.ended_at.desc(), GameSession.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return HistoryPage(items=tuple(_item(game, done) for game, done in rows), total=total)


def _item(game: GameSession, locations_completed: int) -> HistoryItem:
    # every way a game ends sets ended_at, and only finished games are listed
    assert game.ended_at is not None
    return HistoryItem(
        session_id=game.id,
        started_at=game.started_at,
        ended_at=game.ended_at,
        language=Language(game.target_language),
        difficulty=Difficulty(game.difficulty),
        class_id=game.class_id,
        state=SessionState(game.state),
        end_reason=SessionEndReason(game.end_reason) if game.end_reason else None,
        total_score=game.total_score,
        locations_completed=locations_completed,
    )
