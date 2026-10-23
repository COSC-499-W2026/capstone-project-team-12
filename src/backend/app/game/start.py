"""Starting a game (A7, spec Starting a Session; DFD 2.0 process 2.1)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.content.interface import LocationSnapshot
from app.db import violated_constraint
from app.engagement import interface as engagement
from app.errors import AppError, ErrorCode
from app.game.draw import draw_locations
from app.game.sessions import abandon_sessions, is_idle, session_idle_limit
from app.models.accounts import User
from app.models.enums import (
    Difficulty,
    Language,
    LocationStatus,
    SessionEndReason,
    SessionState,
    UserRole,
)
from app.models.game import OPEN_SESSION_STATES, GameSession, SessionLocation
from app.services.audio import get_audio_service
from app.services.locks import lock_user

_ONE_OPEN_GAME = "uq_game_sessions_one_open_per_student"


@dataclass(frozen=True)
class StartedGame:
    """All a student sees of a new game: no names, room numbers or coordinates."""

    session_id: UUID
    state: SessionState
    position: int
    clue_audio_url: str


def start_game(
    user: User,
    difficulty: Difficulty,
    db: Session,
    *,
    class_id: UUID | None = None,
    language: Language | None = None,
) -> StartedGame:
    """Start a class game (`class_id`, the class sets the language) or a Practice game
    (`language`). Pass exactly one of them.

    Raises 409 SESSION_ALREADY_OPEN (details hold the open game's id), NOT_ENOUGH_CONTENT or
    CLASS_ARCHIVED, 404 NOT_FOUND for a class the student is not in, and 403 FORBIDDEN_ROLE
    for an admin. Doesn't commit.
    """
    if (class_id is None) == (language is None):
        raise ValueError("pass either class_id or language")
    if user.role == UserRole.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN_ROLE, "Admins don't play")
    # the same lock as a class or language switch, so the two can't both pass their open-game check;
    # taken before the class is looked up, so what is checked is what is stored once we hold it
    lock_user(user.id, db)
    if class_id is not None:
        language = engagement.require_active_enrolment(user.id, class_id, db)
    assert language is not None

    _check_no_open_game(user.id, db)
    snapshots = draw_locations(user.id, class_id, language, difficulty, db)
    # before the game exists, so a missing recording fails like any other draw problem
    clue_url = get_audio_service().clue_audio_url(snapshots[0].audio_asset_id, db)

    game = _create_game(user.id, class_id, language, difficulty, db)
    db.add_all(_session_locations(game, snapshots))
    db.flush()
    return StartedGame(game.id, SessionState(game.state), game.current_position, clue_url)


def _check_no_open_game(student_id: UUID, db: Session) -> None:
    """Raise SESSION_ALREADY_OPEN if the student has an open game, after expiring it if it has
    been idle too long (spec: expiry is also checked when a new game is started)."""
    game = db.scalar(
        select(GameSession)
        .where(GameSession.student_id == student_id, GameSession.state.in_(OPEN_SESSION_STATES))
        .with_for_update()
    )
    if game is None:
        return
    if not is_idle(game, datetime.now(UTC), session_idle_limit()):
        raise AppError(
            ErrorCode.SESSION_ALREADY_OPEN,
            "Finish or quit your current game first",
            details=[{"session_id": str(game.id)}],
        )
    abandon_sessions(SessionEndReason.EXPIRED, [GameSession.id == game.id], db)


def _create_game(
    student_id: UUID,
    class_id: UUID | None,
    language: Language,
    difficulty: Difficulty,
    db: Session,
) -> GameSession:
    """Save the new game, or raise SESSION_ALREADY_OPEN if another start won the race."""
    game = GameSession(
        student_id=student_id,
        class_id=class_id,
        target_language=language,
        difficulty=difficulty,
        last_activity_at=datetime.now(UTC),
    )
    try:
        # a savepoint, so a lost race leaves the caller's transaction usable
        with db.begin_nested():
            db.add(game)
            db.flush()
    except IntegrityError as error:
        if violated_constraint(error) != _ONE_OPEN_GAME:
            raise
        # another start won the race: report its game
        _check_no_open_game(student_id, db)
        # it has already ended again (e.g. quit straight away), so a retry can succeed
        raise AppError(
            ErrorCode.SESSION_ALREADY_OPEN, "Another game was started at the same time"
        ) from error
    return game


def _session_locations(
    game: GameSession, snapshots: Sequence[LocationSnapshot]
) -> list[SessionLocation]:
    """One row per drawn location with its content snapshot. The first is being navigated."""
    return [
        SessionLocation(
            session_id=game.id,
            location_id=snapshot.location_id,
            position=position,
            status=LocationStatus.NAVIGATING if position == 1 else LocationStatus.PENDING,
            started_at=game.last_activity_at if position == 1 else None,
            clue_id=snapshot.clue_id,
            audio_asset_id=snapshot.audio_asset_id,
            accepted_answers_snapshot=list(snapshot.accepted_answers),
            gps_points_snapshot=[{"lat": p.lat, "lng": p.lng} for p in snapshot.gps_points],
            radius_snapshot=snapshot.radius_meters,
        )
        for position, snapshot in enumerate(snapshots, start=1)
    ]
