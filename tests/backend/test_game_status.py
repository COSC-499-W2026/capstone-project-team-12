"""find_current_game and get_game (A25): a student's open game, or one of their games."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.game.sessions import session_idle_limit
from app.game.status import find_current_game, get_game
from app.models.accounts import User
from app.models.engagement import Class
from app.models.enums import (
    Language,
    LocationStatus,
    SessionEndReason,
    SessionState,
    SkipReason,
)
from app.models.game import OPEN_SESSION_STATES, GameSession, SessionLocation


def _idle_since() -> datetime:
    return datetime.now(UTC) - session_idle_limit() - timedelta(minutes=1)


@pytest.mark.parametrize("state", OPEN_SESSION_STATES)
def test_open_game_is_found_with_its_locations_in_order(
    db: Session,
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    state: SessionState,
) -> None:
    paused_at = datetime.now(UTC) if state == SessionState.PAUSED else None
    game = make_game_session(state=state, paused_at=paused_at, current_position=2, total_score=100)
    make_session_location(game, 2, status=LocationStatus.NAVIGATING)
    make_session_location(
        game,
        1,
        status=LocationStatus.COMPLETED,
        attempts_used=2,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    status = find_current_game(game.student_id, db)

    assert status is not None
    assert (status.session_id, status.state, status.current_position) == (game.id, state, 2)
    assert status.total_score == 100
    assert [(loc.position, loc.status) for loc in status.locations] == [
        (1, LocationStatus.COMPLETED),
        (2, LocationStatus.NAVIGATING),
    ]
    assert (status.locations[0].attempts_used, status.locations[0].points_awarded) == (2, 100)


def test_no_open_game_gives_none(
    db: Session, make_user: Callable[..., User], make_game_session: Callable[..., GameSession]
) -> None:
    student = make_user()
    make_game_session(student, state=SessionState.COMPLETED, completed_at=datetime.now(UTC))

    assert find_current_game(student.id, db) is None
    assert find_current_game(make_user().id, db) is None


def test_idle_game_is_expired_when_read(
    db: Session,
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    game = make_game_session(last_activity_at=_idle_since())
    done = make_session_location(
        game, 1, status=LocationStatus.COMPLETED, points_awarded=100, completed_at=datetime.now(UTC)
    )
    current = make_session_location(game, 2, status=LocationStatus.NAVIGATING)

    assert find_current_game(game.student_id, db) is None

    assert (game.state, game.end_reason) == (SessionState.ABANDONED, SessionEndReason.EXPIRED)
    db.refresh(done)
    db.refresh(current)
    assert done.status == LocationStatus.COMPLETED  # points already earned stay
    assert (current.status, current.skip_reason) == (
        LocationStatus.SKIPPED,
        SkipReason.SESSION_ENDED,
    )


def test_game_played_within_the_limit_is_not_expired(
    db: Session, make_game_session: Callable[..., GameSession]
) -> None:
    recent = datetime.now(UTC) - session_idle_limit() + timedelta(minutes=1)
    game = make_game_session(last_activity_at=recent)

    status = find_current_game(game.student_id, db)

    assert status is not None
    assert status.state == SessionState.ACTIVE


def test_own_finished_game_can_be_read(
    db: Session, make_game_session: Callable[..., GameSession]
) -> None:
    game = make_game_session(state=SessionState.ABANDONED, end_reason=SessionEndReason.QUIT)

    status = get_game(game.student_id, game.id, db)

    assert (status.state, status.end_reason) == (SessionState.ABANDONED, SessionEndReason.QUIT)


def test_another_students_game_looks_like_a_missing_one(
    db: Session, make_user: Callable[..., User], make_game_session: Callable[..., GameSession]
) -> None:
    game = make_game_session()
    stranger = make_user()

    for student_id, game_id in ((stranger.id, game.id), (game.student_id, uuid4())):
        with pytest.raises(AppError) as error:
            get_game(student_id, game_id, db)
        assert error.value.code == ErrorCode.NOT_FOUND


def test_idle_game_read_by_id_comes_back_expired(
    db: Session, make_game_session: Callable[..., GameSession]
) -> None:
    game = make_game_session(last_activity_at=_idle_since())

    status = get_game(game.student_id, game.id, db)

    assert (status.state, status.end_reason) == (SessionState.ABANDONED, SessionEndReason.EXPIRED)


def test_another_students_read_does_not_expire_the_game(
    db: Session, make_user: Callable[..., User], make_game_session: Callable[..., GameSession]
) -> None:
    game = make_game_session(last_activity_at=_idle_since())

    with pytest.raises(AppError):
        get_game(make_user().id, game.id, db)

    db.refresh(game)
    assert game.state == SessionState.ACTIVE


def test_class_game_and_a_closed_class_game_are_read(
    db: Session, make_class: Callable[..., Class], make_game_session: Callable[..., GameSession]
) -> None:
    school_class = make_class(target_language=Language.FR)
    playing = make_game_session(class_id=school_class.id, target_language=Language.FR)
    closed = make_game_session(
        class_id=school_class.id,
        target_language=Language.FR,
        state=SessionState.ABANDONED,
        end_reason=SessionEndReason.CLASS_CLOSED,
    )

    current = find_current_game(playing.student_id, db)
    assert current is not None
    assert (current.class_id, current.language) == (school_class.id, Language.FR)
    ended = get_game(closed.student_id, closed.id, db)
    assert (ended.class_id, ended.end_reason) == (school_class.id, SessionEndReason.CLASS_CLOSED)


def test_game_ended_elsewhere_is_not_reported_as_open(
    db: Session, make_game_session: Callable[..., GameSession]
) -> None:
    game = make_game_session(last_activity_at=_idle_since())
    # another request quits it, without this session's loaded copy seeing the change
    db.execute(
        update(GameSession)
        .where(GameSession.id == game.id)
        .values(state=SessionState.ABANDONED, end_reason=SessionEndReason.QUIT)
        .execution_options(synchronize_session=False)
    )

    status = get_game(game.student_id, game.id, db)

    assert (status.state, status.end_reason) == (SessionState.ABANDONED, SessionEndReason.QUIT)
