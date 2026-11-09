"""reset_game (A9): end the game and start a new one, at most 3 times a local day.

The lookup errors (another student's game, a finished game) and the HTTP layer are checked in
test_game_control.py and test_game_control_api.py.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content import interface as content
from app.errors import AppError, ErrorCode
from app.game import control
from app.game.control import reset_game
from app.game.sessions import session_idle_limit, start_of_today
from app.models.accounts import User
from app.models.engagement import Class
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
from app.services.audio import MediaNotFoundError, UploadedAudioService
from app.services.locks import lock_user

# the modules whose clock reset reads
CLOCK = ("app.game.control", "app.game.status", "app.game.sessions")


class _WithoutRecordings(UploadedAudioService):
    def clue_audio_url(self, audio_asset_id: UUID, db: Session) -> str:
        raise MediaNotFoundError


def _locations(game_id: UUID, db: Session) -> list[SessionLocation]:
    return list(
        db.scalars(
            select(SessionLocation)
            .where(SessionLocation.session_id == game_id)
            .order_by(SessionLocation.position)
        )
    )


def _open_games(student_id: UUID, db: Session) -> list[GameSession]:
    return list(
        db.scalars(
            select(GameSession).where(
                GameSession.student_id == student_id,
                GameSession.state.in_((SessionState.ACTIVE, SessionState.PAUSED)),
            )
        )
    )


@pytest.fixture
def reset_earlier(
    make_game_session: Callable[..., GameSession],
) -> Callable[..., None]:
    """`times` games of the student ended by a reset at `at`."""

    def make(student: User, times: int, at: datetime, **fields: object) -> None:
        for _ in range(times):
            make_game_session(
                student,
                state=SessionState.ABANDONED,
                end_reason=SessionEndReason.RESET,
                last_activity_at=at,
                **fields,
            )

    return make


@pytest.mark.parametrize("in_class", [False, True])
def test_reset_starts_a_new_game_with_the_same_settings(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    freeze_clock: Callable[..., None],
    in_class: bool,
) -> None:
    student = make_user()
    class_id = make_class().id if in_class else None
    old = make_game_session(
        student,
        class_id=class_id,
        difficulty=Difficulty.ADVANCING,
        current_position=2,
        total_score=100,
    )
    done = make_session_location(
        old, 1, status=LocationStatus.COMPLETED, points_awarded=100, completed_at=datetime.now(UTC)
    )
    current = make_session_location(old, 2, status=LocationStatus.NAVIGATING, attempts_used=1)
    reset_at = datetime.now(UTC).replace(microsecond=0)
    freeze_clock(reset_at, *CLOCK)

    started = reset_game(student.id, old.id, db)

    assert (old.state, old.end_reason, old.total_score) == (
        SessionState.ABANDONED,
        SessionEndReason.RESET,
        100,
    )
    # a game action: the daily limit counts the reset by this time
    assert old.last_activity_at == reset_at
    for row in (done, current):
        db.refresh(row)
    assert (done.status, done.points_awarded) == (LocationStatus.COMPLETED, 100)
    assert (current.status, current.skip_reason) == (
        LocationStatus.SKIPPED,
        SkipReason.SESSION_ENDED,
    )

    assert started.session_id != old.id
    assert (started.state, started.position) == (SessionState.ACTIVE, 1)
    new = db.get_one(GameSession, started.session_id)
    assert (new.student_id, new.class_id, new.target_language, new.difficulty) == (
        student.id,
        class_id,
        Language.JA,
        Difficulty.ADVANCING,
    )
    assert (new.state, new.current_position, new.total_score) == (SessionState.ACTIVE, 1, 0)
    locations = _locations(new.id, db)
    assert [location.position for location in locations] == [1, 2, 3, 4, 5]
    assert [location.status for location in locations] == [LocationStatus.NAVIGATING] + [
        LocationStatus.PENDING
    ] * 4
    expected = UploadedAudioService().clue_audio_url(locations[0].audio_asset_id, db)
    assert urlsplit(started.clue_audio_url).path == urlsplit(expected).path
    assert _open_games(student.id, db) == [new]


def test_a_paused_game_is_reset_without_resuming(
    db: Session, make_user: Callable[..., User], make_game_session: Callable[..., GameSession]
) -> None:
    student = make_user()
    paused_at = datetime.now(UTC) - timedelta(hours=1)
    old = make_game_session(
        student, state=SessionState.PAUSED, paused_at=paused_at, last_activity_at=paused_at
    )

    started = reset_game(student.id, old.id, db)

    assert (old.state, old.end_reason, old.paused_at) == (
        SessionState.ABANDONED,
        SessionEndReason.RESET,
        None,
    )
    new = db.get_one(GameSession, started.session_id)
    assert (new.state, new.paused_at) == (SessionState.ACTIVE, None)


def test_reset_does_not_bring_back_a_quit_games_locations(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    student = make_user()
    eligible = sorted(content.eligible_location_ids(Language.JA, Difficulty.BASIC, db))
    quit_earlier = make_game_session(
        student, state=SessionState.ABANDONED, end_reason=SessionEndReason.QUIT
    )
    unplayed = eligible[:2]
    for position, location_id in enumerate(unplayed, start=1):
        make_session_location(
            quit_earlier,
            position,
            location_id=location_id,
            status=LocationStatus.SKIPPED,
            skip_reason=SkipReason.SESSION_ENDED,
        )
    old = make_game_session(student)
    # a random draw always puts them last, so they could only come back through carry-over
    monkeypatch.setattr(
        "app.game.draw.random.shuffle", lambda ids: ids.sort(key=lambda i: i in unplayed)
    )

    started = reset_game(student.id, old.id, db)

    drawn = {location.location_id for location in _locations(started.session_id, db)}
    assert not drawn & set(unplayed)


def test_the_fourth_reset_today_is_refused(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_game_session: Callable[..., GameSession],
    reset_earlier: Callable[..., None],
) -> None:
    # Proposal Test Case 4 (reset part). The limit is per student, in any mode.
    assert get_settings().max_resets_per_day == 3
    student = make_user()
    today = start_of_today()
    reset_earlier(student, 2, today)
    reset_earlier(student, 1, today + timedelta(minutes=5), class_id=make_class().id)
    game = make_game_session(student)

    with pytest.raises(AppError) as error:
        reset_game(student.id, game.id, db)

    assert error.value.code == ErrorCode.RESET_LIMIT_REACHED
    db.refresh(game)
    assert (game.state, game.end_reason) == (SessionState.ACTIVE, None)
    assert _open_games(student.id, db) == [game]


def test_three_resets_a_day_are_allowed(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    reset_earlier: Callable[..., None],
) -> None:
    student = make_user()
    reset_earlier(student, 2, start_of_today())
    game = make_game_session(student)

    third = reset_game(student.id, game.id, db)

    with pytest.raises(AppError) as error:
        reset_game(student.id, third.session_id, db)
    assert error.value.code == ErrorCode.RESET_LIMIT_REACHED


def test_only_todays_resets_by_this_student_count(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    reset_earlier: Callable[..., None],
) -> None:
    student, classmate = make_user(), make_user()
    today = start_of_today()
    # a minute before local midnight is yesterday, even though it may be today in UTC
    reset_earlier(student, 3, today - timedelta(minutes=1))
    reset_earlier(classmate, 3, today)
    # quit and expired games aren't resets
    for reason in (SessionEndReason.QUIT, SessionEndReason.EXPIRED):
        make_game_session(
            student, state=SessionState.ABANDONED, end_reason=reason, last_activity_at=today
        )
    game = make_game_session(student)

    reset_game(student.id, game.id, db)

    assert game.end_reason == SessionEndReason.RESET


def test_the_day_turns_over_at_local_midnight_not_utc(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    reset_earlier: Callable[..., None],
    freeze_clock: Callable[..., None],
) -> None:
    vancouver = ZoneInfo("America/Vancouver")
    midnight = datetime(2026, 10, 28, tzinfo=vancouver)
    # 23:30 the evening before is already Oct 28 in UTC (06:30), but locally it's yesterday
    student = make_user()
    reset_earlier(student, 3, midnight - timedelta(minutes=30))
    now = midnight + timedelta(minutes=30)
    game = make_game_session(student, last_activity_at=now - timedelta(minutes=10))
    freeze_clock(now, *CLOCK)

    reset_game(student.id, game.id, db)
    assert game.end_reason == SessionEndReason.RESET

    # exactly midnight already counts as the new day: 2 more make 3 today
    reset_earlier(student, 2, midnight)
    with pytest.raises(AppError) as error:
        reset_game(student.id, _open_games(student.id, db)[0].id, db)
    assert error.value.code == ErrorCode.RESET_LIMIT_REACHED


def test_not_enough_content_leaves_the_game_as_it_is(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    student = make_user()
    played_at = datetime.now(UTC) - timedelta(hours=1)
    game = make_game_session(student, last_activity_at=played_at)
    four = frozenset(uuid4() for _ in range(4))
    monkeypatch.setattr(content, "eligible_location_ids", lambda *_: four)

    with pytest.raises(AppError) as error:
        reset_game(student.id, game.id, db)

    assert error.value.code == ErrorCode.NOT_ENOUGH_CONTENT
    db.refresh(game)
    assert (game.state, game.end_reason, game.last_activity_at) == (
        SessionState.ACTIVE,
        None,
        played_at,
    )
    assert _open_games(student.id, db) == [game]


def test_a_missing_recording_leaves_the_game_as_it_is(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(control, "get_audio_service", _WithoutRecordings)
    student = make_user()
    game = make_game_session(student)

    with pytest.raises(MediaNotFoundError):
        reset_game(student.id, game.id, db)

    db.refresh(game)
    assert (game.state, game.end_reason) == (SessionState.ACTIVE, None)
    count = select(func.count()).where(GameSession.student_id == student.id)
    assert db.scalar(count) == 1


def test_a_game_idle_for_the_limit_is_expired_not_reset(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    freeze_clock: Callable[..., None],
) -> None:
    now, limit = datetime.now(UTC), session_idle_limit()
    freeze_clock(now, *CLOCK)
    student = make_user()
    game = make_game_session(student, last_activity_at=now - limit)

    with pytest.raises(AppError) as error:
        reset_game(student.id, game.id, db)

    assert error.value.code == ErrorCode.SESSION_NOT_ACTIVE
    # expired, so it isn't counted as a reset and no new game starts
    assert (game.end_reason, game.last_activity_at) == (SessionEndReason.EXPIRED, now - limit)
    assert _open_games(student.id, db) == []


def test_reset_does_not_commit(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # spied directly: the new game's savepoint fires the same session events as a commit
    calls: list[str] = []
    monkeypatch.setattr(db, "commit", lambda: calls.append("commit"))
    monkeypatch.setattr(db, "rollback", lambda: calls.append("rollback"))
    student = make_user()
    game = make_game_session(student)

    reset_game(student.id, game.id, db)

    assert calls == []


def test_reset_holds_the_students_lock_until_the_transaction_ends(test_engine: Engine) -> None:
    """A start or a class switch takes the student's lock first, so a reset has to take it too,
    in the same order (the student, then the game), or the two could deadlock. The setup
    commits, unlike the other tests, so the lock is visible to another connection."""
    student_id = game_id = None
    holder: Session | None = None
    try:
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
                last_activity_at=datetime.now(UTC),
            )
            setup.add(game)
            setup.flush()
            student_id, game_id = student.id, game.id
            setup.commit()
        holder = Session(test_engine)
        reset_game(student_id, game_id, holder)
        with Session(test_engine) as waiting:
            # statement_timeout is a backstop: lock_timeout 0 would wait forever
            waiting.execute(text("SET LOCAL lock_timeout = '500ms'"))
            waiting.execute(text("SET LOCAL statement_timeout = '2s'"))
            with pytest.raises(OperationalError) as blocked:
                lock_user(student_id, waiting)
            # 55P03 lock_not_available: the reset still holds the row
            assert getattr(blocked.value.orig, "sqlstate", None) == "55P03"
    finally:
        if holder is not None:
            holder.rollback()
            holder.close()
        if game_id is not None:
            with Session(test_engine) as cleanup:
                cleanup.execute(text("SET LOCAL lock_timeout = '10s'"))
                cleanup.execute(delete(GameSession).where(GameSession.id == game_id))
                cleanup.execute(delete(User).where(User.id == student_id))
                cleanup.commit()
