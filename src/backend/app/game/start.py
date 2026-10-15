"""Starting a game (A7, spec Starting a Session; DFD 2.0 process 2.1)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.engagement import interface as engagement
from app.errors import AppError, ErrorCode
from app.game.draw import draw_locations
from app.models.accounts import User
from app.models.enums import (
    Difficulty,
    Language,
    LocationStatus,
    SessionEndReason,
    SessionState,
    SkipReason,
    UserRole,
)
from app.models.game import OPEN_SESSION_STATES, GameSession, SessionLocation
from app.services.audio import clue_audio_url

_ONE_OPEN_GAME = "uq_game_sessions_one_open_per_student"
_UNFINISHED = (LocationStatus.PENDING, LocationStatus.NAVIGATING, LocationStatus.VERIFYING)


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
    if class_id is not None:
        language = _class_language(user, class_id, db)
    assert language is not None

    _check_no_open_game(user.id, db)
    snapshots = draw_locations(user.id, class_id, language, difficulty, db)

    game = GameSession(
        student_id=user.id,
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
        if _ONE_OPEN_GAME not in str(error.orig):
            raise
        # another start won the race: report its game
        _check_no_open_game(user.id, db)
        # it has already ended again (e.g. quit straight away), so a retry can succeed
        raise AppError(
            ErrorCode.SESSION_ALREADY_OPEN, "Another game was started at the same time"
        ) from error

    for position, snapshot in enumerate(snapshots, start=1):
        db.add(
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
        )
    db.flush()
    return StartedGame(
        session_id=game.id,
        state=SessionState.ACTIVE,
        position=1,
        clue_audio_url=clue_audio_url(snapshots[0].audio_asset_id, db),
    )


def _class_language(user: User, class_id: UUID, db: Session) -> Language:
    """The class language, if the user is an active student of a class that isn't archived."""
    language = engagement.get_class_language(class_id, db)
    # same answer for a class that doesn't exist and one the student isn't in
    if language is None:
        raise AppError(ErrorCode.NOT_FOUND, "Class not found")
    if engagement.is_class_archived(class_id, db):
        raise AppError(ErrorCode.CLASS_ARCHIVED)
    if not engagement.is_actively_enrolled(user.id, class_id, db):
        raise AppError(ErrorCode.NOT_FOUND, "Class not found")
    return language


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
    idle_limit = timedelta(hours=get_settings().session_idle_expiry_hours)
    if game.last_activity_at > datetime.now(UTC) - idle_limit:
        raise AppError(
            ErrorCode.SESSION_ALREADY_OPEN,
            "Finish or quit your current game first",
            details=[{"session_id": str(game.id)}],
        )
    _expire(game, db)


def _expire(game: GameSession, db: Session) -> None:
    # last_activity_at stays: expiring isn't something the student did
    game.state = SessionState.ABANDONED
    game.end_reason = SessionEndReason.EXPIRED
    game.paused_at = None
    db.execute(
        update(SessionLocation)
        .where(SessionLocation.session_id == game.id, SessionLocation.status.in_(_UNFINISHED))
        .values(status=LocationStatus.SKIPPED, skip_reason=SkipReason.SESSION_ENDED)
    )
    db.flush()
