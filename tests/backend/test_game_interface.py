"""end_open_sessions ends the right games when a class closes; get_open_session_id finds a
student's open game."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.game.interface import end_open_sessions, get_open_session_id
from app.game.sessions import session_idle_limit
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
    school_class, other_class = make_class(), make_class()
    last_played = datetime(2026, 10, 1, 12, tzinfo=UTC)
    game = make_game_session(
        class_id=school_class.id,
        current_position=3,
        total_score=100,
        last_activity_at=last_played,
    )
    done = make_session_location(
        game, 1, status=LocationStatus.COMPLETED, points_awarded=100, completed_at=datetime.now(UTC)
    )
    failed = make_session_location(game, 2, status=LocationStatus.FAILED, attempts_used=6)
    current = make_session_location(game, 3, status=LocationStatus.NAVIGATING)
    later = make_session_location(game, 4)
    # a game in another class must keep its locations as they are
    other_game = make_game_session(class_id=other_class.id)
    other_current = make_session_location(other_game, 1, status=LocationStatus.NAVIGATING)
    other_later = make_session_location(other_game, 2)

    end_open_sessions(school_class.id, db)
    for row in (game, done, failed, current, later, other_current, other_later):
        db.refresh(row)
    assert (done.status, done.points_awarded) == (LocationStatus.COMPLETED, 100)
    assert (failed.status, failed.skip_reason) == (LocationStatus.FAILED, None)
    for row in (current, later):
        assert (row.status, row.skip_reason) == (LocationStatus.SKIPPED, SkipReason.SESSION_ENDED)
    assert other_current.status == LocationStatus.NAVIGATING
    assert other_later.status == LocationStatus.PENDING
    assert game.total_score == 100
    # closing the class isn't student activity, so this stays (now() would be today)
    assert game.last_activity_at == last_played


def test_open_game_is_found_whether_active_or_paused(
    db: Session, make_user: Callable[..., User], make_game_session: Callable[..., GameSession]
) -> None:
    playing, paused = make_user(), make_user()
    active_game = make_game_session(playing)
    paused_game = make_game_session(paused, state=SessionState.PAUSED, paused_at=datetime.now(UTC))

    assert get_open_session_id(playing.id, db) == active_game.id
    assert get_open_session_id(paused.id, db) == paused_game.id


def test_no_open_game_gives_none(
    db: Session, make_user: Callable[..., User], make_game_session: Callable[..., GameSession]
) -> None:
    student, never_played, someone_else = make_user(), make_user(), make_user()
    make_game_session(student, state=SessionState.COMPLETED, completed_at=datetime.now(UTC))
    make_game_session(student, state=SessionState.ABANDONED, end_reason=SessionEndReason.QUIT)
    make_game_session(someone_else)

    assert get_open_session_id(student.id, db) is None
    assert get_open_session_id(never_played.id, db) is None


def test_a_game_idle_for_the_expiry_time_does_not_count(
    db: Session, make_user: Callable[..., User], make_game_session: Callable[..., GameSession]
) -> None:
    limit = session_idle_limit()
    now = datetime.now(UTC)
    just_inside, at_the_limit, long_idle = make_user(), make_user(), make_user()
    inside_game = make_game_session(
        just_inside, last_activity_at=now - limit + timedelta(minutes=1)
    )
    # exactly the limit counts as idle, the same boundary as is_idle (<=)
    make_game_session(at_the_limit, last_activity_at=now - limit)
    make_game_session(long_idle, last_activity_at=now - limit - timedelta(hours=1))

    assert get_open_session_id(just_inside.id, db) == inside_game.id
    assert get_open_session_id(at_the_limit.id, db) is None
    assert get_open_session_id(long_idle.id, db) is None


def test_looking_for_an_open_game_only_reads(
    db: Session,
    transaction_calls: list[str],
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
) -> None:
    student = make_user()
    make_game_session(student)
    transaction_calls.clear()

    get_open_session_id(student.id, db)

    assert transaction_calls == []
