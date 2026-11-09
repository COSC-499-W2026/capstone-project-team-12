"""pause_game and resume_game (A8): the service rules. The lookup errors are checked for
quit_game and reset_game (A9) too; their own rules are in test_quit_game.py and
test_reset_game.py."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Engine, delete, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.game.control import pause_game, quit_game, reset_game, resume_game
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

# the modules whose clock pause and resume read
CLOCK = ("app.game.control", "app.game.status")

ENDED = [
    {"state": SessionState.COMPLETED, "completed_at": datetime.now(UTC)},
    *({"state": SessionState.ABANDONED, "end_reason": reason} for reason in SessionEndReason),
]


def _paused(make_game_session: Callable[..., GameSession], **fields: object) -> GameSession:
    return make_game_session(state=SessionState.PAUSED, paused_at=datetime.now(UTC), **fields)


def _lock_timeout(error: OperationalError) -> bool:
    # 55P03 lock_not_available: the other transaction still holds the row
    return getattr(error.orig, "sqlstate", None) == "55P03"


def test_pause_and_resume_keep_the_stage_and_attempts(
    db: Session,
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    freeze_clock: Callable[..., None],
) -> None:
    started = datetime.now(UTC) - timedelta(hours=1)
    game = make_game_session(current_position=2, last_activity_at=started)
    make_session_location(
        game, 1, status=LocationStatus.COMPLETED, points_awarded=100, completed_at=started
    )
    make_session_location(game, 2, status=LocationStatus.VERIFYING, attempts_used=3)
    paused_at = datetime.now(UTC).replace(microsecond=0)
    freeze_clock(paused_at, *CLOCK)

    paused = pause_game(game.student_id, game.id, db)

    assert (game.state, game.paused_at, game.last_activity_at) == (
        SessionState.PAUSED,
        paused_at,
        paused_at,
    )
    assert paused.status.state == SessionState.PAUSED
    assert paused.status.paused_at == paused_at
    assert paused.expires_at == paused_at + session_idle_limit()

    resumed_at = paused_at + timedelta(hours=2)
    freeze_clock(resumed_at, *CLOCK)
    status = resume_game(game.student_id, game.id, db)

    assert (game.state, game.paused_at, game.last_activity_at) == (
        SessionState.ACTIVE,
        None,
        resumed_at,
    )
    assert game.resume_count == 1
    assert status.paused_at is None
    assert status.current_position == 2
    assert [(loc.status, loc.attempts_used) for loc in status.locations] == [
        (LocationStatus.COMPLETED, 0),
        (LocationStatus.VERIFYING, 3),
    ]


def test_each_resume_adds_one_to_resume_count(
    db: Session,
    make_game_session: Callable[..., GameSession],
    freeze_clock: Callable[..., None],
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    game = make_game_session(last_activity_at=now)
    for expected in (1, 2, 3):
        paused_at = now + timedelta(hours=expected)
        freeze_clock(paused_at, *CLOCK)
        paused = pause_game(game.student_id, game.id, db)
        assert paused.status.paused_at == paused_at
        freeze_clock(paused_at + timedelta(minutes=1), *CLOCK)
        status = resume_game(game.student_id, game.id, db)
        assert (game.resume_count, status.paused_at) == (expected, None)


def test_pausing_again_changes_nothing(
    db: Session, make_game_session: Callable[..., GameSession]
) -> None:
    paused_at = datetime.now(UTC) - timedelta(hours=3)
    game = _paused(make_game_session, last_activity_at=paused_at)
    game.paused_at = paused_at
    db.flush()

    paused = pause_game(game.student_id, game.id, db)

    assert (game.state, game.paused_at, game.last_activity_at) == (
        SessionState.PAUSED,
        paused_at,
        paused_at,
    )
    # still counted from the first pause
    assert paused.expires_at == paused_at + session_idle_limit()


def test_resuming_an_active_game_changes_nothing(
    db: Session, make_game_session: Callable[..., GameSession]
) -> None:
    played_at = datetime.now(UTC) - timedelta(hours=3)
    game = make_game_session(last_activity_at=played_at)

    status = resume_game(game.student_id, game.id, db)

    assert status.state == SessionState.ACTIVE
    assert (game.resume_count, game.last_activity_at) == (0, played_at)


@pytest.mark.parametrize("fields", ENDED)
@pytest.mark.parametrize("action", [pause_game, resume_game, quit_game, reset_game])
def test_a_finished_game_is_not_active(
    db: Session,
    make_game_session: Callable[..., GameSession],
    action: Callable[..., object],
    fields: dict[str, object],
) -> None:
    game = make_game_session(**fields)

    with pytest.raises(AppError) as error:
        action(game.student_id, game.id, db)

    assert error.value.code == ErrorCode.SESSION_NOT_ACTIVE
    assert error.value.details == [{"session_id": str(game.id), "state": fields["state"]}]
    db.refresh(game)
    assert (game.state, game.paused_at) == (fields["state"], None)


@pytest.mark.parametrize("action", [pause_game, resume_game, quit_game, reset_game])
def test_another_students_game_or_an_unknown_id_is_not_found(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    action: Callable[..., object],
) -> None:
    idle_since = datetime.now(UTC) - session_idle_limit() - timedelta(minutes=1)
    games = [make_game_session(last_activity_at=idle_since), _paused(make_game_session)]
    stranger = make_user()

    for game in games:
        for student_id, game_id in ((stranger.id, game.id), (game.student_id, uuid4())):
            with pytest.raises(AppError) as error:
                action(student_id, game_id, db)
            assert error.value.code == ErrorCode.NOT_FOUND

    # neither paused, resumed nor expired
    for game, state in zip(games, (SessionState.ACTIVE, SessionState.PAUSED), strict=True):
        db.refresh(game)
        assert (game.state, game.end_reason) == (state, None)


@pytest.mark.parametrize(
    ("action", "state"),
    [(resume_game, SessionState.PAUSED), (pause_game, SessionState.ACTIVE)],
)
def test_a_game_idle_for_the_limit_is_expired_instead(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    action: Callable[..., object],
    state: SessionState,
    freeze_clock: Callable[..., None],
    transaction_calls: list[str],
) -> None:
    now, limit = datetime.now(UTC), session_idle_limit()
    freeze_clock(now, *CLOCK)
    paused_at = now - limit if state == SessionState.PAUSED else None
    # exactly the limit counts as idle, the same boundary as is_idle (<=)
    at_the_limit = make_game_session(state=state, paused_at=paused_at, last_activity_at=now - limit)
    done = make_session_location(
        at_the_limit, 1, status=LocationStatus.COMPLETED, points_awarded=100, completed_at=now
    )
    current = make_session_location(at_the_limit, 2, status=LocationStatus.NAVIGATING)
    just_inside = make_game_session(
        state=state, paused_at=paused_at, last_activity_at=now - limit + timedelta(minutes=1)
    )

    transaction_calls.clear()
    with pytest.raises(AppError) as error:
        action(at_the_limit.student_id, at_the_limit.id, db)

    assert error.value.code == ErrorCode.SESSION_NOT_ACTIVE
    # expired in this session, still uncommitted, so the caller can commit before the 409
    assert "commit" not in transaction_calls
    assert (at_the_limit.state, at_the_limit.end_reason, at_the_limit.paused_at) == (
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
    # a minute inside the limit is still played
    action(just_inside.student_id, just_inside.id, db)
    assert just_inside.end_reason is None


def test_pausing_starts_the_idle_time_again(
    db: Session,
    make_game_session: Callable[..., GameSession],
    freeze_clock: Callable[..., None],
) -> None:
    now, limit = datetime.now(UTC), session_idle_limit()
    freeze_clock(now, *CLOCK)
    game = make_game_session(last_activity_at=now - limit + timedelta(minutes=1))

    paused = pause_game(game.student_id, game.id, db)

    assert paused.expires_at == now + limit
    # a minute later the old activity time would have expired it
    freeze_clock(now + timedelta(minutes=1), *CLOCK)
    resume_game(game.student_id, game.id, db)
    assert (game.state, game.end_reason) == (SessionState.ACTIVE, None)


def test_pause_locks_the_callers_row_until_the_transaction_ends(test_engine: Engine) -> None:
    """pause_game keeps FOR UPDATE until the caller ends the transaction.

    The game is already paused, so this call writes nothing and the lock is only the SELECT
    FOR UPDATE. Another student's lookup locks nothing, so a second connection can still take
    the row. The setup commits, unlike the other tests, so the lock is visible to another
    connection.
    """
    now = datetime.now(UTC)
    student_id = stranger_id = game_id = None
    holder: Session | None = None
    lookup: Session | None = None
    try:
        with Session(test_engine) as setup:
            student = User(
                email=f"{uuid4().hex[:12]}@example.com",
                username=f"user_{uuid4().hex[:8]}",
                role=UserRole.STUDENT,
            )
            stranger = User(
                email=f"{uuid4().hex[:12]}@example.com",
                username=f"user_{uuid4().hex[:8]}",
                role=UserRole.STUDENT,
            )
            setup.add_all([student, stranger])
            setup.flush()
            game = GameSession(
                student_id=student.id,
                target_language=Language.JA,
                difficulty=Difficulty.BASIC,
                state=SessionState.PAUSED,
                paused_at=now,
                last_activity_at=now,
            )
            setup.add(game)
            setup.flush()
            student_id, stranger_id, game_id = student.id, stranger.id, game.id
            setup.commit()
        holder = Session(test_engine)
        pause_game(student_id, game_id, holder)
        with Session(test_engine) as waiting:
            # statement_timeout is a backstop: lock_timeout 0 would wait forever
            waiting.execute(text("SET LOCAL lock_timeout = '500ms'"))
            waiting.execute(text("SET LOCAL statement_timeout = '2s'"))
            with pytest.raises(OperationalError) as blocked:
                waiting.scalar(
                    select(GameSession).where(GameSession.id == game_id).with_for_update()
                )
            assert _lock_timeout(blocked.value)
        holder.rollback()
        holder.close()
        holder = None

        lookup = Session(test_engine)
        with pytest.raises(AppError) as missing:
            pause_game(stranger_id, game_id, lookup)
        assert missing.value.code == ErrorCode.NOT_FOUND
        with Session(test_engine) as other:
            other.execute(text("SET LOCAL lock_timeout = '500ms'"))
            other.execute(text("SET LOCAL statement_timeout = '2s'"))
            other.scalar(select(GameSession).where(GameSession.id == game_id).with_for_update())
    finally:
        if holder is not None:
            holder.rollback()
            holder.close()
        if lookup is not None:
            lookup.rollback()
            lookup.close()
        if game_id is not None:
            with Session(test_engine) as cleanup:
                cleanup.execute(text("SET LOCAL lock_timeout = '10s'"))
                cleanup.execute(delete(GameSession).where(GameSession.id == game_id))
                cleanup.execute(delete(User).where(User.id.in_((student_id, stranger_id))))
                cleanup.commit()


def test_a_game_ended_elsewhere_is_not_resumed(
    db: Session, make_game_session: Callable[..., GameSession]
) -> None:
    game = _paused(make_game_session)
    # another request quits it, without this session's loaded copy seeing the change
    db.execute(
        update(GameSession)
        .where(GameSession.id == game.id)
        .values(state=SessionState.ABANDONED, end_reason=SessionEndReason.QUIT, paused_at=None)
        .execution_options(synchronize_session=False)
    )

    with pytest.raises(AppError) as error:
        resume_game(game.student_id, game.id, db)

    assert error.value.code == ErrorCode.SESSION_NOT_ACTIVE
    assert (game.state, game.end_reason) == (SessionState.ABANDONED, SessionEndReason.QUIT)


@pytest.mark.parametrize("action", [pause_game, resume_game, quit_game])
def test_game_actions_do_not_commit(
    db: Session,
    make_game_session: Callable[..., GameSession],
    transaction_calls: list[str],
    action: Callable[..., object],
) -> None:
    game = make_game_session()
    game_to_resume = _paused(make_game_session)
    transaction_calls.clear()

    action(game.student_id, game.id, db)
    action(game_to_resume.student_id, game_to_resume.id, db)

    assert "commit" not in transaction_calls
    assert "rollback" not in transaction_calls
