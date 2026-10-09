"""end_open_sessions ends the right games when a class closes."""

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.game.interface import end_open_sessions
from app.models.accounts import User
from app.models.engagement import Class
from app.models.enums import LocationStatus, SessionEndReason, SessionState, SkipReason
from app.models.game import GameSession, SessionLocation


def test_archiving_ends_every_open_game_in_the_class(
    db: Session,
    make_class: Callable[..., Class],
    make_game_session: Callable[..., GameSession],
) -> None:
    school_class, other_class = make_class(), make_class()
    active = make_game_session(class_id=school_class.id)
    paused = make_game_session(
        class_id=school_class.id, state=SessionState.PAUSED, paused_at=datetime.now(UTC)
    )
    elsewhere = make_game_session(class_id=other_class.id)
    practice = make_game_session()

    assert end_open_sessions(school_class.id, db) == 2
    for game in (active, paused, elsewhere, practice):
        db.refresh(game)
    for game in (active, paused):
        assert game.state == SessionState.ABANDONED
        assert game.end_reason == SessionEndReason.CLASS_CLOSED
        assert game.paused_at is None
    assert elsewhere.state == practice.state == SessionState.ACTIVE


def test_leaving_ends_only_that_students_game(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_game_session: Callable[..., GameSession],
) -> None:
    school_class, leaving = make_class(), make_user()
    theirs = make_game_session(leaving, class_id=school_class.id)
    classmate = make_game_session(class_id=school_class.id)

    assert end_open_sessions(school_class.id, db, student_id=leaving.id) == 1
    db.refresh(theirs)
    db.refresh(classmate)
    assert theirs.state == SessionState.ABANDONED
    assert classmate.state == SessionState.ACTIVE


def test_finished_games_are_left_alone(
    db: Session, make_class: Callable[..., Class], make_game_session: Callable[..., GameSession]
) -> None:
    school_class = make_class()
    done = make_game_session(
        class_id=school_class.id, state=SessionState.COMPLETED, completed_at=datetime.now(UTC)
    )
    assert end_open_sessions(school_class.id, db) == 0
    db.refresh(done)
    assert done.state == SessionState.COMPLETED


def test_unfinished_locations_are_skipped_and_points_kept(
    db: Session,
    make_class: Callable[..., Class],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    school_class = make_class()
    game = make_game_session(class_id=school_class.id, current_position=2, total_score=100)
    done = make_session_location(
        game, 1, status=LocationStatus.COMPLETED, points_awarded=100, completed_at=datetime.now(UTC)
    )
    current = make_session_location(game, 2, status=LocationStatus.NAVIGATING)
    later = make_session_location(game, 3)

    end_open_sessions(school_class.id, db)
    for row in (game, done, current, later):
        db.refresh(row)
    assert (done.status, done.points_awarded) == (LocationStatus.COMPLETED, 100)
    for row in (current, later):
        assert (row.status, row.skip_reason) == (LocationStatus.SKIPPED, SkipReason.SESSION_ENDED)
    assert game.total_score == 100
