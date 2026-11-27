"""A student's games for the export of their account: every game, finished ones too, with every
location row it has had, rows a shuffle replaced included.

Each location keeps the student's results and their last GPS position. The content snapshot
(accepted answers, entrance points, radius, clue and recording) is left out because it is campus
content, not the student's data, and so is the navigation state (reference point, last feedback
phrase).
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.game.progress import LIVE
from app.models.enums import (
    ArrivalOverride,
    Difficulty,
    Language,
    LocationStatus,
    SessionEndReason,
    SessionState,
    SkipReason,
)
from app.models.game import GameSession, SessionLocation


@dataclass(frozen=True)
class ExportedGameLocation:
    location_id: UUID
    position: int
    status: LocationStatus
    skip_reason: SkipReason | None
    started_at: datetime | None
    arrived_at: datetime | None
    arrival_override: ArrivalOverride | None
    attempts_used: int
    points_awarded: int
    flags: tuple[str, ...]
    completed_at: datetime | None
    # the last GPS position sent at this location; no track is kept
    last_lat: float | None
    last_lng: float | None
    last_accuracy_m: float | None
    last_ping_at: datetime | None


@dataclass(frozen=True)
class ExportedGame:
    id: UUID
    class_id: UUID | None  # None for Practice
    target_language: Language
    difficulty: Difficulty
    state: SessionState
    end_reason: SessionEndReason | None
    started_at: datetime
    paused_at: datetime | None
    completed_at: datetime | None
    last_activity_at: datetime
    resume_count: int
    shuffles_used: int
    total_score: int
    locations: tuple[ExportedGameLocation, ...]


def list_games_for_export(student_id: UUID, db: Session) -> list[ExportedGame]:
    """The student's games, oldest first, each with its locations in game order.

    At a position a shuffle changed, the rows it replaced come before the live row. Which of two
    replaced rows came first isn't stored, so they are in no set order. Read only.
    """
    games = list(
        db.scalars(
            select(GameSession)
            .where(GameSession.student_id == student_id)
            .order_by(GameSession.started_at, GameSession.id)
        )
    )
    # every game's locations in one query, not one per game
    rows = db.scalars(
        select(SessionLocation)
        .join(GameSession, GameSession.id == SessionLocation.session_id)
        .where(GameSession.student_id == student_id)
        # false sorts first, so the replaced rows come before the live one
        .order_by(SessionLocation.position, LIVE, SessionLocation.id)
    )
    locations: defaultdict[UUID, list[ExportedGameLocation]] = defaultdict(list)
    for row in rows:
        locations[row.session_id].append(_location(row))
    return [_game(game, tuple(locations[game.id])) for game in games]


def _game(game: GameSession, locations: tuple[ExportedGameLocation, ...]) -> ExportedGame:
    return ExportedGame(
        id=game.id,
        class_id=game.class_id,
        target_language=Language(game.target_language),
        difficulty=Difficulty(game.difficulty),
        state=SessionState(game.state),
        end_reason=SessionEndReason(game.end_reason) if game.end_reason else None,
        started_at=game.started_at,
        paused_at=game.paused_at,
        completed_at=game.completed_at,
        last_activity_at=game.last_activity_at,
        resume_count=game.resume_count,
        shuffles_used=game.shuffles_used,
        total_score=game.total_score,
        locations=locations,
    )


def _location(row: SessionLocation) -> ExportedGameLocation:
    return ExportedGameLocation(
        location_id=row.location_id,
        position=row.position,
        status=LocationStatus(row.status),
        skip_reason=SkipReason(row.skip_reason) if row.skip_reason else None,
        started_at=row.started_at,
        arrived_at=row.arrived_at,
        arrival_override=ArrivalOverride(row.arrival_override) if row.arrival_override else None,
        attempts_used=row.attempts_used,
        points_awarded=row.points_awarded,
        flags=tuple(row.flags),
        completed_at=row.completed_at,
        last_lat=row.last_lat,
        last_lng=row.last_lng,
        last_accuracy_m=row.last_accuracy_m,
        last_ping_at=row.last_ping_at,
    )
