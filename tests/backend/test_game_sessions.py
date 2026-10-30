"""abandon_sessions ends only the open games that match, and the idle rule is the same for one
loaded game (is_idle) and for a query (idle_condition)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.game.sessions import abandon_sessions, idle_condition, is_idle, start_of_today
from app.models.enums import LocationStatus, SessionEndReason, SessionState, SkipReason
from app.models.game import GameSession, SessionLocation

IDLE_LIMIT = timedelta(hours=24)


@pytest.mark.parametrize("state", [SessionState.ACTIVE, SessionState.PAUSED])
def test_an_open_game_is_abandoned_and_its_unfinished_locations_skipped(
    db: Session,
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    state: SessionState,
) -> None:
    now = datetime.now(UTC)
    game = make_game_session(state=state, paused_at=now if state == SessionState.PAUSED else None)
    done = make_session_location(
        game, 1, status=LocationStatus.COMPLETED, points_awarded=100, completed_at=now
    )
    current = make_session_location(game, 2, status=LocationStatus.NAVIGATING, started_at=now)
    later = make_session_location(game, 3)

    ended = abandon_sessions(SessionEndReason.EXPIRED, [GameSession.id == game.id], db)

    assert ended == [game.id]
    for row in (game, done, current, later):
        db.refresh(row)
    assert (game.state, game.end_reason) == (SessionState.ABANDONED, SessionEndReason.EXPIRED)
    assert game.paused_at is None
    assert done.status == LocationStatus.COMPLETED and done.points_awarded == 100
    for location in (current, later):
        assert (location.status, location.skip_reason) == (
            LocationStatus.SKIPPED,
            SkipReason.SESSION_ENDED,
        )


def test_last_activity_at_changes_only_for_an_end_the_student_asked_for(
    db: Session, make_game_session: Callable[..., GameSession]
) -> None:
    # quit and reset pass the time; a class closing or an expiry keeps the old value
    played_at = datetime.now(UTC) - timedelta(hours=2)
    quit_at = played_at + timedelta(hours=1)
    by_student = make_game_session(last_activity_at=played_at)
    by_system = make_game_session(last_activity_at=played_at)

    abandon_sessions(
        SessionEndReason.QUIT, [GameSession.id == by_student.id], db, last_activity_at=quit_at
    )
    abandon_sessions(SessionEndReason.CLASS_CLOSED, [GameSession.id == by_system.id], db)

    db.refresh(by_student)
    db.refresh(by_system)
    assert (by_student.end_reason, by_student.last_activity_at) == (SessionEndReason.QUIT, quit_at)
    assert (by_system.end_reason, by_system.last_activity_at) == (
        SessionEndReason.CLASS_CLOSED,
        played_at,
    )


def test_a_game_that_already_ended_is_left_alone(
    db: Session,
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    # Negative: a game that has already ended matches the condition but must not change.
    now = datetime.now(UTC)
    completed = make_game_session(state=SessionState.COMPLETED, completed_at=now)
    quit_game = make_game_session(state=SessionState.ABANDONED, end_reason=SessionEndReason.QUIT)
    left_pending = make_session_location(quit_game, 1)

    ended = abandon_sessions(
        SessionEndReason.EXPIRED, [GameSession.id.in_([completed.id, quit_game.id])], db
    )

    assert ended == []
    for row in (completed, quit_game, left_pending):
        db.refresh(row)
    assert (completed.state, completed.end_reason) == (SessionState.COMPLETED, None)
    assert (quit_game.state, quit_game.end_reason) == (
        SessionState.ABANDONED,
        SessionEndReason.QUIT,
    )
    assert left_pending.status == LocationStatus.PENDING


def test_only_the_games_that_match_are_ended(
    db: Session, make_game_session: Callable[..., GameSession]
) -> None:
    mine, other = make_game_session(), make_game_session()

    ended = abandon_sessions(SessionEndReason.EXPIRED, [GameSession.id == mine.id], db)

    db.refresh(mine)
    db.refresh(other)
    assert ended == [mine.id]
    assert (mine.state, other.state) == (SessionState.ABANDONED, SessionState.ACTIVE)


def test_no_condition_is_refused_instead_of_ending_every_game(
    db: Session, make_game_session: Callable[..., GameSession]
) -> None:
    game = make_game_session()

    with pytest.raises(ValueError, match="at least one condition"):
        abandon_sessions(SessionEndReason.EXPIRED, [], db)

    db.refresh(game)
    assert game.state == SessionState.ACTIVE


@pytest.mark.parametrize(
    ("idle_for", "expected"),
    [
        (timedelta(hours=23, minutes=59), False),
        (timedelta(hours=24), True),
        (timedelta(hours=24, minutes=1), True),
    ],
    ids=["23h59m", "24h", "24h01m"],
)
def test_a_game_is_idle_from_the_limit_on(
    db: Session,
    make_game_session: Callable[..., GameSession],
    idle_for: timedelta,
    expected: bool,
) -> None:
    now = datetime.now(UTC)
    game = make_game_session(last_activity_at=now - idle_for)

    assert is_idle(game, now, IDLE_LIMIT) is expected
    # the query used to find games to expire gives the same answer
    in_query = db.scalar(
        select(GameSession.id).where(GameSession.id == game.id, idle_condition(now, IDLE_LIMIT))
    )
    assert (in_query == game.id) is expected


@pytest.mark.parametrize(
    "local_now",
    [
        datetime(2026, 10, 28, 0, 30),
        # 07:30 UTC, already the next day in UTC
        datetime(2026, 10, 28, 23, 30),
        # the night daylight saving ends: 01:30 happens twice
        datetime(2026, 11, 1, 1, 30, fold=1),
    ],
)
def test_today_starts_at_local_midnight(
    local_now: datetime, freeze_clock: Callable[..., None]
) -> None:
    vancouver = ZoneInfo("America/Vancouver")
    freeze_clock(local_now.replace(tzinfo=vancouver), "app.game.sessions")

    start = start_of_today()

    assert start == datetime(local_now.year, local_now.month, local_now.day, tzinfo=vancouver)
    assert (start.hour, start.minute) == (0, 0)
