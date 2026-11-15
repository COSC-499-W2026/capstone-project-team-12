"""quit_game (A9): ending a game keeps its points and lets the next game carry its locations.

The lookup errors (another student's game, a finished game, no commit) are checked with pause
and resume in test_game_control.py.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.content import interface as content
from app.errors import AppError, ErrorCode
from app.game.control import quit_game
from app.game.draw import draw_locations
from app.game.sessions import session_idle_limit
from app.models.accounts import User
from app.models.enums import (
    Difficulty,
    Language,
    LocationStatus,
    SessionEndReason,
    SessionState,
    SkipReason,
)
from app.models.game import GameSession, SessionLocation

# the modules whose clock quit reads
CLOCK = ("app.game.control", "app.game.status")


def test_quit_keeps_points(
    db: Session,
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    freeze_clock: Callable[..., None],
) -> None:
    # Proposal Test Case 1: the game is ended and the points already earned are kept
    started = datetime.now(UTC) - timedelta(hours=1)
    game = make_game_session(current_position=2, total_score=100, last_activity_at=started)
    done = make_session_location(
        game, 1, status=LocationStatus.COMPLETED, points_awarded=100, completed_at=started
    )
    current = make_session_location(game, 2, status=LocationStatus.VERIFYING, attempts_used=2)
    later = make_session_location(game, 3)
    quit_at = datetime.now(UTC).replace(microsecond=0)
    freeze_clock(quit_at, *CLOCK)

    status = quit_game(game.student_id, game.id, db)

    assert (game.state, game.end_reason) == (SessionState.ABANDONED, SessionEndReason.QUIT)
    # the carry-over draw reads the quit time from here
    assert game.last_activity_at == quit_at
    assert (status.state, status.end_reason, status.total_score) == (
        SessionState.ABANDONED,
        SessionEndReason.QUIT,
        100,
    )
    for row in (done, current, later):
        db.refresh(row)
    assert (done.status, done.points_awarded) == (LocationStatus.COMPLETED, 100)
    # attempts already used stay with the location
    assert (current.status, current.skip_reason, current.attempts_used) == (
        LocationStatus.SKIPPED,
        SkipReason.SESSION_ENDED,
        2,
    )
    assert (later.status, later.skip_reason) == (LocationStatus.SKIPPED, SkipReason.SESSION_ENDED)
    assert [(loc.status, loc.points_awarded) for loc in status.locations] == [
        (LocationStatus.COMPLETED, 100),
        (LocationStatus.SKIPPED, 0),
        (LocationStatus.SKIPPED, 0),
    ]


def test_a_paused_game_is_quit_without_resuming(
    db: Session, make_game_session: Callable[..., GameSession]
) -> None:
    paused_at = datetime.now(UTC) - timedelta(hours=1)
    game = make_game_session(
        state=SessionState.PAUSED, paused_at=paused_at, last_activity_at=paused_at
    )

    status = quit_game(game.student_id, game.id, db)

    assert (game.state, game.end_reason, game.paused_at) == (
        SessionState.ABANDONED,
        SessionEndReason.QUIT,
        None,
    )
    assert game.resume_count == 0
    assert game.last_activity_at > paused_at
    assert status.paused_at is None


def test_quitting_twice_is_not_active(
    db: Session, make_game_session: Callable[..., GameSession]
) -> None:
    game = make_game_session()
    quit_game(game.student_id, game.id, db)
    quit_at = game.last_activity_at

    with pytest.raises(AppError) as error:
        quit_game(game.student_id, game.id, db)

    assert error.value.code == ErrorCode.SESSION_NOT_ACTIVE
    assert error.value.details == [{"session_id": str(game.id), "state": SessionState.ABANDONED}]
    # the second call doesn't move the quit time
    assert game.last_activity_at == quit_at


@pytest.mark.parametrize("state", [SessionState.ACTIVE, SessionState.PAUSED])
def test_a_game_idle_for_the_limit_is_expired_not_quit(
    db: Session,
    make_game_session: Callable[..., GameSession],
    freeze_clock: Callable[..., None],
    state: SessionState,
) -> None:
    now, limit = datetime.now(UTC), session_idle_limit()
    freeze_clock(now, *CLOCK)
    paused_at = now - limit if state == SessionState.PAUSED else None
    at_the_limit = make_game_session(state=state, paused_at=paused_at, last_activity_at=now - limit)
    just_inside = make_game_session(
        state=state, paused_at=paused_at, last_activity_at=now - limit + timedelta(minutes=1)
    )

    with pytest.raises(AppError) as error:
        quit_game(at_the_limit.student_id, at_the_limit.id, db)

    assert error.value.code == ErrorCode.SESSION_NOT_ACTIVE
    # expired, so a quit today can't bring its locations back, and the idle time stays
    assert (at_the_limit.end_reason, at_the_limit.last_activity_at) == (
        SessionEndReason.EXPIRED,
        now - limit,
    )
    quit_game(just_inside.student_id, just_inside.id, db)
    assert (just_inside.end_reason, just_inside.last_activity_at) == (SessionEndReason.QUIT, now)


def test_a_game_ended_elsewhere_is_not_quit(
    db: Session, make_game_session: Callable[..., GameSession]
) -> None:
    game = make_game_session()
    # another request completes it, without this session's loaded copy seeing the change
    completed_at = datetime.now(UTC)
    db.execute(
        update(GameSession)
        .where(GameSession.id == game.id)
        .values(state=SessionState.COMPLETED, completed_at=completed_at)
        .execution_options(synchronize_session=False)
    )

    with pytest.raises(AppError) as error:
        quit_game(game.student_id, game.id, db)

    assert error.value.code == ErrorCode.SESSION_NOT_ACTIVE
    assert (game.state, game.end_reason) == (SessionState.COMPLETED, None)


@pytest.mark.parametrize("state", [SessionState.ACTIVE, SessionState.PAUSED])
def test_the_next_game_today_starts_with_the_unplayed_locations(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    state: SessionState,
) -> None:
    # quit is what A11's carry-over looks for; a reset or an expiry wouldn't count
    eligible: list[UUID] = sorted(content.eligible_location_ids(Language.JA, Difficulty.BASIC, db))
    student = make_user()
    now = datetime.now(UTC)
    game = make_game_session(
        student,
        state=state,
        paused_at=now if state == SessionState.PAUSED else None,
        current_position=2,
    )
    make_session_location(
        game,
        1,
        location_id=eligible[0],
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=now,
    )
    for position in range(2, 6):
        make_session_location(game, position, location_id=eligible[position - 1])

    quit_game(student.id, game.id, db)
    drawn = draw_locations(student.id, None, Language.JA, Difficulty.BASIC, db)

    assert [snapshot.location_id for snapshot in drawn][:4] == eligible[1:5]
