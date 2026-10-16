"""abandon_sessions ends only open games, and is_idle decides when an open game expires."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.game.sessions import abandon_sessions, is_idle
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

    abandon_sessions([game.id], SessionEndReason.EXPIRED, db)

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


def test_a_game_that_already_ended_is_left_alone(
    db: Session,
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    # Negative: the ids may include a game that ended between choosing it and ending it.
    now = datetime.now(UTC)
    completed = make_game_session(state=SessionState.COMPLETED, completed_at=now)
    quit_game = make_game_session(state=SessionState.ABANDONED, end_reason=SessionEndReason.QUIT)
    left_pending = make_session_location(quit_game, 1)

    abandon_sessions([completed.id, quit_game.id], SessionEndReason.EXPIRED, db)

    for row in (completed, quit_game, left_pending):
        db.refresh(row)
    assert (completed.state, completed.end_reason) == (SessionState.COMPLETED, None)
    assert (quit_game.state, quit_game.end_reason) == (
        SessionState.ABANDONED,
        SessionEndReason.QUIT,
    )
    assert left_pending.status == LocationStatus.PENDING


def test_no_ids_changes_nothing(db: Session, make_game_session: Callable[..., GameSession]) -> None:
    game = make_game_session()

    abandon_sessions([], SessionEndReason.EXPIRED, db)

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
    make_game_session: Callable[..., GameSession], idle_for: timedelta, expected: bool
) -> None:
    now = datetime.now(UTC)
    game = make_game_session(last_activity_at=now - idle_for)

    assert is_idle(game, now, IDLE_LIMIT) is expected
