"""The game side of a photo result (A10 TC1 and TC2, A8 and A9 notes): locking the location
before the photo is read, then counting the attempt, scoring and moving on."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content import interface as content
from app.errors import AppError, ErrorCode
from app.game.control import quit_game
from app.game.current_location import Stage
from app.game.photo import PhotoProgress, finish_photo_location, lock_photo_location
from app.game.shuffle import shuffle_game
from app.models.accounts import User
from app.models.engagement import PointsLedger
from app.models.enums import Difficulty, Language, LocationStatus, SessionState, SkipReason
from app.models.game import GameSession, SessionLocation

NOW = datetime(2026, 11, 11, 20, 0, tzinfo=UTC)
AN_HOUR_AGO = NOW - timedelta(hours=1)
CLOCK = ("app.game.photo", "app.game.control", "app.game.status")


@pytest.fixture
def verifying_game(
    db: Session,
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    freeze_clock: Callable[..., None],
) -> Callable[..., GameSession]:
    """A game with `locations` locations, at the first one, which is in Stage 2; the others are
    pending. All are active (stub) locations, so moving on doesn't skip them. The last game
    action was an hour ago; the clock is frozen at NOW. Other keywords are for the game."""

    def make(locations: int = 2, attempts_used: int = 0, **fields: Any) -> GameSession:
        freeze_clock(NOW, *CLOCK)
        if fields.get("state") == SessionState.PAUSED:
            fields.setdefault("paused_at", AN_HOUR_AGO)
        game = make_game_session(last_activity_at=AN_HOUR_AGO, **fields)
        active = sorted(content.eligible_location_ids(Language.JA, Difficulty.BASIC, db))
        make_session_location(
            game,
            1,
            location_id=active[0],
            status=LocationStatus.VERIFYING,
            started_at=NOW - timedelta(minutes=5),
            arrived_at=NOW - timedelta(minutes=1),
            attempts_used=attempts_used,
        )
        for position in range(2, locations + 1):
            make_session_location(game, position, location_id=active[position - 1])
        db.flush()
        return game

    return make


@pytest.fixture
def statements(db: Session) -> Iterator[list[str]]:
    """The SQL the session sends, in order."""
    seen: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        seen.append(statement)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", record)
    yield seen
    event.remove(bind, "before_cursor_execute", record)


def row_at(game: GameSession, position: int, db: Session) -> SessionLocation:
    """The live (not shuffled) row at a position."""
    return db.scalars(
        select(SessionLocation).where(
            SessionLocation.session_id == game.id,
            SessionLocation.position == position,
            SessionLocation.skip_reason.is_distinct_from(SkipReason.SHUFFLED),
        )
    ).one()


def lock(game: GameSession, db: Session) -> SessionLocation:
    return lock_photo_location(game.student_id, game.id, db)


def finish(
    game: GameSession, matched: bool, db: Session, row: SessionLocation | None = None
) -> PhotoProgress:
    """Finish the current location (or `row`), which is expected to take the result."""
    row = row or row_at(game, game.current_position, db)
    progress = finish_photo_location(game.student_id, game.id, row.id, matched, db)
    assert progress is not None
    return progress


def outcome(progress: PhotoProgress) -> tuple[LocationStatus, int, int, int]:
    return progress.status, progress.attempts_used, progress.attempts_left, progress.points_awarded


def upcoming(progress: PhotoProgress) -> tuple[int, Stage] | None:
    following = progress.next_location
    return None if following is None else (following.position, following.stage)


def refused(call: Callable[[], object]) -> ErrorCode:
    with pytest.raises(AppError) as error:
        call()
    return error.value.code


def ledger_rows(row: SessionLocation, db: Session) -> int:
    awards = db.scalars(select(PointsLedger).where(PointsLedger.source_ref == str(row.id)))
    return len(awards.all())


def _first_index(statements: list[str], table: str, lock_clause: str) -> int:
    return next(i for i, s in enumerate(statements) if f"FROM {table}" in s and lock_clause in s)


STEPS: dict[str, Callable[[GameSession, Session], object]] = {
    "lock": lock,
    "finish": lambda game, db: finish(game, True, db),
}


@pytest.mark.parametrize("step", STEPS.values(), ids=STEPS.keys())
def test_both_steps_lock_the_user_then_the_game_then_the_location(
    db: Session,
    verifying_game: Callable[..., GameSession],
    statements: list[str],
    step: Callable[[GameSession, Session], object],
) -> None:
    # the order of start, reset and scoring; the other order could deadlock with them
    game = verifying_game()
    statements.clear()

    step(game, db)

    user = _first_index(statements, "users", "FOR NO KEY UPDATE")
    locked_game = _first_index(statements, "game_sessions", "FOR UPDATE")
    location = _first_index(statements, "session_locations", "FOR UPDATE")
    assert user < locked_game < location


def test_neither_step_commits_or_rolls_back(
    db: Session, verifying_game: Callable[..., GameSession], transaction_calls: list[str]
) -> None:
    game = verifying_game()

    finish(game, True, db, lock(game, db))

    assert "commit" not in transaction_calls
    assert "rollback" not in transaction_calls


# lock_photo_location


@pytest.mark.parametrize("attempts_used", [0, 5])
def test_lock_returns_the_location_in_stage_2_and_counts_as_a_game_action(
    db: Session, verifying_game: Callable[..., GameSession], attempts_used: int
) -> None:
    game = verifying_game(attempts_used=attempts_used)

    row = lock(game, db)

    assert row.id == row_at(game, 1, db).id
    assert row.attempts_used == attempts_used  # only a result uses an attempt
    assert game.last_activity_at == NOW


@pytest.mark.parametrize(
    ("fields", "status", "code"),
    [
        ({"state": SessionState.PAUSED}, LocationStatus.VERIFYING, ErrorCode.SESSION_NOT_ACTIVE),
        (
            {"state": SessionState.COMPLETED, "completed_at": AN_HOUR_AGO},
            LocationStatus.VERIFYING,
            ErrorCode.SESSION_NOT_ACTIVE,
        ),
        ({}, LocationStatus.NAVIGATING, ErrorCode.INVALID_STAGE),
        ({"attempts_used": 6}, LocationStatus.VERIFYING, ErrorCode.ATTEMPTS_EXHAUSTED),
    ],
)
def test_lock_refuses_a_new_photo_and_writes_nothing(
    db: Session,
    verifying_game: Callable[..., GameSession],
    fields: dict[str, Any],
    status: LocationStatus,
    code: ErrorCode,
) -> None:
    game = verifying_game(**fields)
    row_at(game, 1, db).status = status
    db.flush()

    assert refused(lambda: lock(game, db)) == code
    assert game.last_activity_at == AN_HOUR_AGO


def test_lock_refuses_another_students_game(
    db: Session, verifying_game: Callable[..., GameSession], make_user: Callable[..., User]
) -> None:
    game = verifying_game()

    error = refused(lambda: lock_photo_location(make_user().id, game.id, db))

    assert error == ErrorCode.NOT_FOUND


# finish_photo_location


def test_a_match_completes_the_location_scores_it_and_moves_on(
    db: Session, verifying_game: Callable[..., GameSession]
) -> None:
    game = verifying_game()
    row = lock(game, db)  # the route locks, reads the photo, then finishes in one transaction

    progress = finish(game, True, db, row)

    assert outcome(progress) == (LocationStatus.COMPLETED, 1, 5, 100)
    assert progress.engagement is not None
    assert progress.engagement.location_points_awarded == 100
    assert (row.points_awarded, row.completed_at, ledger_rows(row, db)) == (100, NOW, 1)
    assert game.total_score == 100
    assert upcoming(progress) == (2, Stage.NAVIGATION)
    assert progress.game_state == SessionState.ACTIVE
    following = row_at(game, 2, db)
    assert (following.status, following.started_at) == (LocationStatus.NAVIGATING, NOW)


def test_a_miss_uses_an_attempt_and_stays_in_stage_2(
    db: Session, verifying_game: Callable[..., GameSession]
) -> None:
    game = verifying_game(attempts_used=3)

    progress = finish(game, False, db)

    assert not db.dirty  # flushed: the app's sessions don't autoflush before the next read
    assert outcome(progress) == (LocationStatus.VERIFYING, 4, 2, 0)
    assert (progress.engagement, upcoming(progress)) == (None, None)
    row = row_at(game, 1, db)
    assert (row.status, row.attempts_used, ledger_rows(row, db)) == (LocationStatus.VERIFYING, 4, 0)
    assert game.current_position == 1
    assert game.last_activity_at == AN_HOUR_AGO  # a result arriving isn't a game action


def test_the_last_attempt_without_a_match_fails_the_location_and_moves_on(
    db: Session, verifying_game: Callable[..., GameSession]
) -> None:
    game = verifying_game(attempts_used=5)

    progress = finish(game, False, db)

    assert outcome(progress) == (LocationStatus.FAILED, 6, 0, 0)
    assert progress.engagement is not None
    assert progress.engagement.location_points_awarded == 0
    row = row_at(game, 1, db)
    assert (row.completed_at, ledger_rows(row, db)) == (None, 0)
    assert upcoming(progress) == (2, Stage.NAVIGATION)


def test_a_match_on_the_last_attempt_still_completes_the_location(
    db: Session, verifying_game: Callable[..., GameSession]
) -> None:
    game = verifying_game(attempts_used=5)

    assert outcome(finish(game, True, db)) == (LocationStatus.COMPLETED, 6, 0, 100)


def test_the_attempt_limit_comes_from_the_settings(
    db: Session,
    verifying_game: Callable[..., GameSession],
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    monkeypatch.setenv("MAX_PHOTO_ATTEMPTS", "3")
    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)  # the next caller reads the restored env
    failing, used_up = verifying_game(attempts_used=2), verifying_game(attempts_used=3)

    # with the spec value (6) the first would stay in Stage 2 and the second could still upload
    assert finish(failing, False, db).status == LocationStatus.FAILED
    assert refused(lambda: lock(used_up, db)) == ErrorCode.ATTEMPTS_EXHAUSTED


def test_a_match_at_the_last_location_completes_the_game(
    db: Session, verifying_game: Callable[..., GameSession]
) -> None:
    game = verifying_game(locations=1)

    progress = finish(game, True, db)

    assert (progress.game_state, upcoming(progress)) == (SessionState.COMPLETED, None)
    assert (game.state, game.completed_at, game.total_score) == (SessionState.COMPLETED, NOW, 100)


@pytest.mark.parametrize(
    ("locations", "state", "paused_at"),
    [
        (2, SessionState.PAUSED, AN_HOUR_AGO),  # stays paused at the next location
        (1, SessionState.COMPLETED, None),  # the last location completes it
    ],
)
def test_a_paused_game_still_takes_the_result(
    db: Session,
    verifying_game: Callable[..., GameSession],
    locations: int,
    state: SessionState,
    paused_at: datetime | None,
) -> None:
    # A8: the student paused while the photo was being read
    game = verifying_game(locations=locations, state=SessionState.PAUSED)

    assert finish(game, True, db).status == LocationStatus.COMPLETED

    assert (game.state, game.paused_at, game.total_score) == (state, paused_at, 100)


def test_a_location_replaced_by_a_shuffle_gets_nothing(
    db: Session, verifying_game: Callable[..., GameSession]
) -> None:
    # A9: shuffled while the photo was being read; the result must not reach the new location
    game = verifying_game()
    photographed = lock(game, db)
    shuffle_game(game.student_id, game.id, 0, db)

    progress = finish_photo_location(game.student_id, game.id, photographed.id, True, db)

    assert progress is None
    assert (photographed.status, photographed.attempts_used) == (LocationStatus.SKIPPED, 0)
    assert ledger_rows(photographed, db) == 0
    replacement = row_at(game, 1, db)
    assert (replacement.status, replacement.attempts_used) == (LocationStatus.NAVIGATING, 0)
    assert (game.current_position, game.total_score) == (1, 0)


def test_a_game_that_ended_meanwhile_refuses_the_result(
    db: Session, verifying_game: Callable[..., GameSession]
) -> None:
    game = verifying_game()
    photographed = lock(game, db)
    quit_game(game.student_id, game.id, db)

    assert refused(lambda: finish(game, True, db, photographed)) == ErrorCode.SESSION_NOT_ACTIVE
    assert (photographed.attempts_used, ledger_rows(photographed, db)) == (0, 0)
