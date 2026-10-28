"""The expire-sessions job (A8): expire_idle_sessions and the command that runs it."""

import logging
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session

import app.cli
from app.game.interface import expire_idle_sessions
from app.game.sessions import session_idle_limit
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
from app.models.game import GameSession, SessionLocation

CRONTAB = Path(app.cli.__file__).parents[1] / "crontab"


def _idle_since() -> datetime:
    return datetime.now(UTC) - session_idle_limit() - timedelta(minutes=1)


@pytest.fixture(autouse=True)
def no_idle_games(db: Session) -> None:
    """Expire idle games already in the database (e.g. a developer's own data) inside the test's
    transaction, so the counts below only see the games each test makes. Rolled back after."""
    expire_idle_sessions(db, batch_size=1_000_000)


@pytest.fixture
def run_job(db: Session, monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    """Run `python -m app.cli expire-sessions` on the test session."""
    monkeypatch.setattr(app.cli, "SessionLocal", lambda: nullcontext(db))
    return lambda: app.cli.main(["expire-sessions"])


def test_idle_active_and_paused_games_expire(
    db: Session,
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    active = make_game_session(last_activity_at=_idle_since())
    paused = make_game_session(
        state=SessionState.PAUSED, paused_at=_idle_since(), last_activity_at=_idle_since()
    )
    done = make_session_location(
        active, 1, status=LocationStatus.COMPLETED, points_awarded=100, completed_at=_idle_since()
    )
    current = make_session_location(active, 2, status=LocationStatus.NAVIGATING)

    assert expire_idle_sessions(db, batch_size=10) == 2

    for game in (active, paused):
        db.refresh(game)
        assert (game.state, game.end_reason, game.paused_at) == (
            SessionState.ABANDONED,
            SessionEndReason.EXPIRED,
            None,
        )
    db.refresh(done)
    db.refresh(current)
    assert (done.status, done.points_awarded) == (LocationStatus.COMPLETED, 100)
    assert (current.status, current.skip_reason) == (
        LocationStatus.SKIPPED,
        SkipReason.SESSION_ENDED,
    )


def test_only_games_idle_for_the_limit_expire(
    db: Session,
    make_game_session: Callable[..., GameSession],
    freeze_clock: Callable[..., None],
) -> None:
    now, limit = datetime.now(UTC), session_idle_limit()
    freeze_clock(now, "app.game.sessions")
    # exactly the limit counts as idle, the same boundary as is_idle (<=)
    at_the_limit = make_game_session(last_activity_at=now - limit)
    just_inside = make_game_session(last_activity_at=now - limit + timedelta(minutes=1))
    finished = [
        make_game_session(
            state=SessionState.COMPLETED, completed_at=now, last_activity_at=now - 2 * limit
        ),
        make_game_session(
            state=SessionState.ABANDONED,
            end_reason=SessionEndReason.QUIT,
            last_activity_at=now - 2 * limit,
        ),
    ]

    assert expire_idle_sessions(db, batch_size=10) == 1

    db.refresh(at_the_limit)
    db.refresh(just_inside)
    assert at_the_limit.end_reason == SessionEndReason.EXPIRED
    assert just_inside.state == SessionState.ACTIVE
    for game, end_reason in zip(finished, (None, SessionEndReason.QUIT), strict=True):
        db.refresh(game)
        assert game.end_reason == end_reason


def test_finished_games_do_not_take_up_the_batch(
    db: Session, make_game_session: Callable[..., GameSession]
) -> None:
    # comes first in id order, but it has already ended
    make_game_session(
        id=UUID(int=0),
        state=SessionState.ABANDONED,
        end_reason=SessionEndReason.QUIT,
        last_activity_at=_idle_since(),
    )
    idle = make_game_session(last_activity_at=_idle_since())

    assert expire_idle_sessions(db, batch_size=1) == 1
    db.refresh(idle)
    assert idle.end_reason == SessionEndReason.EXPIRED


def test_a_game_another_request_has_locked_is_skipped(test_engine: Engine) -> None:
    # needs real, committed rows and two connections, so it doesn't use the db fixture
    with Session(test_engine) as setup:
        student = User(
            email=f"{uuid4().hex[:12]}@example.com",
            username=f"user_{uuid4().hex[:8]}",
            role=UserRole.STUDENT,
        )
        setup.add(student)
        setup.flush()
        game = GameSession(
            student_id=student.id,
            target_language=Language.JA,
            difficulty=Difficulty.BASIC,
            last_activity_at=_idle_since(),
        )
        setup.add(game)
        setup.commit()
        game_id, student_id = game.id, student.id
    other_request, job = Session(test_engine), Session(test_engine)
    try:
        # e.g. a pause holding the game
        other_request.execute(
            select(GameSession).where(GameSession.id == game_id).with_for_update()
        )
        # fail instead of hanging if the job waits for the lock
        job.execute(text("SET LOCAL lock_timeout = '2s'"))

        expire_idle_sessions(job, batch_size=1_000_000)

        locked = job.get(GameSession, game_id)
        assert locked is not None
        assert locked.state == SessionState.ACTIVE
        job.rollback()
        other_request.rollback()
        # the next run expires it
        expire_idle_sessions(job, batch_size=1_000_000)
        released = job.get(GameSession, game_id)
        assert released is not None
        assert released.end_reason == SessionEndReason.EXPIRED
    finally:
        # nothing the job did is committed; remove the rows this test committed
        job.rollback()
        other_request.rollback()
        job.close()
        other_request.close()
        with Session(test_engine) as cleanup:
            cleanup.execute(delete(GameSession).where(GameSession.id == game_id))
            cleanup.execute(delete(User).where(User.id == student_id))
            cleanup.commit()


def test_one_call_expires_at_most_one_batch(
    db: Session, make_game_session: Callable[..., GameSession]
) -> None:
    games = [make_game_session(last_activity_at=_idle_since()) for _ in range(3)]

    assert expire_idle_sessions(db, batch_size=2) == 2
    assert expire_idle_sessions(db, batch_size=2) == 1
    assert expire_idle_sessions(db, batch_size=2) == 0
    for game in games:
        db.refresh(game)
        assert game.end_reason == SessionEndReason.EXPIRED


def test_the_command_runs_until_every_idle_game_is_expired(
    db: Session,
    make_game_session: Callable[..., GameSession],
    run_job: Callable[[], int],
    transaction_calls: list[str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(app.cli, "EXPIRE_BATCH_SIZE", 2)
    games = [make_game_session(last_activity_at=_idle_since()) for _ in range(5)]
    transaction_calls.clear()

    with caplog.at_level(logging.INFO, logger="app.cli"):
        assert run_job() == 0

    # batches of 2, 2 and 1, each committed
    assert transaction_calls.count("commit") == 3
    for game in games:
        db.refresh(game)
        assert game.end_reason == SessionEndReason.EXPIRED
    assert "expired 5 games" in caplog.text


def test_the_command_logs_nothing_when_no_game_is_idle(
    make_game_session: Callable[..., GameSession],
    run_job: Callable[[], int],
    caplog: pytest.LogCaptureFixture,
) -> None:
    make_game_session()

    with caplog.at_level(logging.INFO, logger="app.cli"):
        assert run_job() == 0

    assert "expire-sessions" not in caplog.text


def test_every_job_in_the_crontab_is_a_command() -> None:
    jobs = [
        line.split("app.cli", 1)[1].strip()
        for line in CRONTAB.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

    assert "expire-sessions" in jobs
    assert set(jobs) <= set(app.cli.COMMANDS)


@pytest.mark.parametrize("batch_size", [0, -1])
def test_a_non_positive_batch_is_refused(
    db: Session, make_game_session: Callable[..., GameSession], batch_size: int
) -> None:
    game = make_game_session(last_activity_at=_idle_since())

    with pytest.raises(ValueError, match="positive"):
        expire_idle_sessions(db, batch_size=batch_size)

    db.refresh(game)
    assert game.state == SessionState.ACTIVE


def test_expire_sessions_runs_every_five_minutes() -> None:
    schedule = {
        line.split("app.cli", 1)[1].strip(): " ".join(line.split()[:5])
        for line in CRONTAB.read_text().splitlines()
        if line.strip() and not line.startswith("#") and "app.cli" in line
    }

    assert schedule["expire-sessions"] == "*/5 * * * *"
