"""The game side of a photo result (A10 TC1 and TC2, A8 and A9 notes): locking the game for an
Idempotency-Key lookup and the location before the photo is read, then counting the attempt,
scoring and moving on."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.game.control import quit_game
from app.game.current_location import Stage
from app.game.photo import (
    PhotographedLocation,
    PhotoProgress,
    apply_photo_result,
    lock_photo_game,
    lock_photo_location,
    lock_photographed_location,
)
from app.game.shuffle import shuffle_game
from app.models.engagement import Achievement, PointsLedger, UserAchievement
from app.models.enums import (
    Difficulty,
    Language,
    LocationStatus,
    SessionEndReason,
    SessionState,
    SkipReason,
)
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
    playable_location_ids: Callable[..., list[UUID]],
) -> Callable[..., GameSession]:
    """A game with `locations` locations, at the first one, which is in Stage 2; the others are
    pending. All are playable ja/basic locations, so moving on doesn't skip them. Last game
    action an hour ago unless given; clock frozen at NOW. Other keywords are for the game."""

    def make(locations: int = 2, attempts_used: int = 0, **fields: Any) -> GameSession:
        freeze_clock(NOW, *CLOCK)
        if fields.get("state") == SessionState.PAUSED:
            fields.setdefault("paused_at", AN_HOUR_AGO)
        game = make_game_session(**{"last_activity_at": AN_HOUR_AGO, **fields})
        active = playable_location_ids(Language.JA, Difficulty.BASIC)
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


def target_of(game: GameSession, row: SessionLocation, db: Session) -> PhotographedLocation | None:
    """The lock step after OCR for `row`; None means the route drops the result."""
    return lock_photographed_location(game.student_id, game.id, row.id, db)


def finish(
    game: GameSession, matched: bool, db: Session, row: SessionLocation | None = None
) -> PhotoProgress:
    """Apply a result to the current location (or `row`) as the route does after OCR: the lock
    step, then `apply_photo_result`. The location is expected to take the result."""
    target = target_of(game, row or row_at(game, game.current_position, db), db)
    assert target is not None
    return apply_photo_result(target, matched, db)


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
    "lock photographed": lambda game, db: target_of(game, row_at(game, 1, db), db),
}


@pytest.mark.parametrize("step", STEPS.values(), ids=STEPS.keys())
def test_each_step_locks_the_user_then_the_game_then_the_location(
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


def test_no_step_commits_or_rolls_back(
    db: Session, verifying_game: Callable[..., GameSession], transaction_calls: list[str]
) -> None:
    game = verifying_game()

    lock_photo_game(game.student_id, game.id, db)
    finish(game, True, db, lock(game, db))  # lock_photographed_location, apply_photo_result

    assert "commit" not in transaction_calls
    assert "rollback" not in transaction_calls


# lock_photo_game


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"state": SessionState.PAUSED},
        {"state": SessionState.COMPLETED, "completed_at": AN_HOUR_AGO},
        {"state": SessionState.ABANDONED, "end_reason": SessionEndReason.QUIT},
        {"last_activity_at": NOW - timedelta(days=3)},  # only a new upload expires it
    ],
    ids=["active", "paused", "completed", "abandoned", "idle"],
)
def test_the_game_lock_takes_a_game_in_any_state_and_changes_nothing(
    db: Session,
    verifying_game: Callable[..., GameSession],
    statements: list[str],
    fields: dict[str, Any],
) -> None:
    # an Idempotency-Key is looked up before any state check, so a replay works after the end
    game = verifying_game(**fields)
    before = (game.state, game.last_activity_at)
    statements.clear()

    lock_photo_game(game.student_id, game.id, db)

    assert (game.state, game.last_activity_at) == before
    user = _first_index(statements, "users", "FOR NO KEY UPDATE")
    assert user < _first_index(statements, "game_sessions", "FOR UPDATE")
    assert not any("session_locations" in statement for statement in statements)


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


# lock_photographed_location and apply_photo_result


def test_a_match_completes_the_location_scores_it_and_moves_on(
    db: Session, verifying_game: Callable[..., GameSession]
) -> None:
    game = verifying_game()
    row = lock(game, db)  # before OCR; `finish` runs the steps after it

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
    game = verifying_game(attempts_used=4)  # the 5th miss: one before the limit

    progress = finish(game, False, db)

    assert not db.dirty  # flushed: the app's sessions don't autoflush before the next read
    assert outcome(progress) == (LocationStatus.VERIFYING, 5, 1, 0)
    assert (progress.engagement, upcoming(progress)) == (None, None)
    row = row_at(game, 1, db)
    assert (row.status, row.attempts_used, ledger_rows(row, db)) == (LocationStatus.VERIFYING, 5, 0)
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


def test_the_lock_step_counts_nothing_and_a_result_is_applied_only_in_stage_2(
    db: Session, verifying_game: Callable[..., GameSession]
) -> None:
    game = verifying_game()  # the route checks the attempt's OCR run between the two steps
    target = target_of(game, row_at(game, 1, db), db)
    assert target is not None and (target.game, target.row.attempts_used) == (game, 0)

    apply_photo_result(target, True, db)

    with pytest.raises(ValueError, match="Stage 2"):
        apply_photo_result(target, True, db)
    row = target.row
    assert (row.status, row.attempts_used, game.total_score) == (LocationStatus.COMPLETED, 1, 100)


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


@pytest.mark.parametrize("state", [SessionState.ACTIVE, SessionState.PAUSED])
def test_a_match_at_the_last_location_completes_the_game(
    db: Session, verifying_game: Callable[..., GameSession], state: SessionState
) -> None:
    # A8: a game paused while the photo was being read is completed too, and no longer paused
    game = verifying_game(locations=1, state=state)

    progress = finish(game, True, db)

    assert (progress.game_state, upcoming(progress)) == (SessionState.COMPLETED, None)
    assert (game.state, game.completed_at, game.paused_at) == (SessionState.COMPLETED, NOW, None)
    assert game.total_score == 100


@pytest.mark.parametrize(
    ("first_status", "shuffled", "expected_unlock"),
    [
        (LocationStatus.COMPLETED, False, True),
        (LocationStatus.COMPLETED, True, True),
        (LocationStatus.FAILED, False, False),
        (LocationStatus.SKIPPED, False, False),
    ],
    ids=["all-completed", "shuffled-out-row", "failed", "skipped"],
)
def test_perfect_session_uses_the_five_live_location_results(
    db: Session,
    verifying_game: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    first_status: LocationStatus,
    shuffled: bool,
    expected_unlock: bool,
) -> None:
    game = verifying_game(locations=5)
    for position in range(1, 4):
        row = row_at(game, position, db)
        row.status = first_status if position == 1 else LocationStatus.COMPLETED
        if row.status == LocationStatus.COMPLETED:
            row.points_awarded = 100
            row.completed_at = NOW - timedelta(minutes=10)
        elif row.status == LocationStatus.SKIPPED:
            row.skip_reason = SkipReason.DEACTIVATED
    if shuffled:
        make_session_location(
            game, 1, status=LocationStatus.SKIPPED, skip_reason=SkipReason.SHUFFLED
        )

    game.current_position = 4
    fourth = row_at(game, 4, db)
    fourth.status = LocationStatus.VERIFYING
    fourth.arrived_at = NOW
    db.flush()
    progress = finish(game, True, db)
    assert progress.engagement is not None
    assert "perfect_session" not in progress.engagement.achievements_unlocked

    fifth = row_at(game, 5, db)
    fifth.status = LocationStatus.VERIFYING
    fifth.arrived_at = NOW
    db.flush()
    progress = finish(game, True, db)
    assert progress.game_state == SessionState.COMPLETED
    assert progress.engagement is not None
    assert ("perfect_session" in progress.engagement.achievements_unlocked) is expected_unlock
    assert refused(lambda: target_of(game, fifth, db)) == ErrorCode.SESSION_NOT_ACTIVE

    languages = db.scalars(
        select(UserAchievement.language)
        .join(Achievement, Achievement.id == UserAchievement.achievement_id)
        .where(
            UserAchievement.user_id == game.student_id,
            Achievement.achievement_key == "perfect_session",
        )
    ).all()
    assert languages == ([Language.JA] if expected_unlock else [])


def test_a_paused_game_takes_the_result_and_stays_paused(
    db: Session, verifying_game: Callable[..., GameSession]
) -> None:
    # A8: the student paused while the photo was being read
    game = verifying_game(state=SessionState.PAUSED)

    assert finish(game, True, db).status == LocationStatus.COMPLETED

    assert (game.state, game.paused_at, game.total_score) == (SessionState.PAUSED, AN_HOUR_AGO, 100)


def test_the_lock_step_drops_a_result_for_a_location_replaced_by_a_shuffle(
    db: Session, verifying_game: Callable[..., GameSession]
) -> None:
    # A9: shuffled while the photo was being read. None makes the route drop the result, so it
    # never reaches the new location (route test: test_photo_api.py, "shuffled")
    game = verifying_game()
    photographed = lock(game, db)
    shuffle_game(game.student_id, game.id, 0, db)

    assert target_of(game, photographed, db) is None


@pytest.mark.parametrize(("attempts_used", "matched"), [(0, True), (5, False)])
def test_the_lock_step_drops_a_second_result_for_a_finished_location(
    db: Session, verifying_game: Callable[..., GameSession], attempts_used: int, matched: bool
) -> None:
    # completed, then failed. A result applied twice under one lock is refused by
    # apply_photo_result itself (test_the_lock_step_counts_nothing_and_...)
    game = verifying_game(locations=3, attempts_used=attempts_used)
    finished = row_at(game, 1, db)
    finish(game, matched, db, finished)

    assert target_of(game, finished, db) is None


def test_another_students_game_is_never_touched(
    db: Session, verifying_game: Callable[..., GameSession]
) -> None:
    game, other_game = verifying_game(), verifying_game()
    others = row_at(other_game, 1, db)

    error = refused(lambda: lock_photo_location(game.student_id, other_game.id, db))
    game_error = refused(lambda: lock_photo_game(game.student_id, other_game.id, db))
    target = target_of(game, others, db)

    assert (error, game_error, target) == (ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND, None)
    assert (others.status, others.attempts_used) == (LocationStatus.VERIFYING, 0)
    assert (other_game.last_activity_at, other_game.total_score) == (AN_HOUR_AGO, 0)


def test_a_game_that_ended_meanwhile_refuses_the_result(
    db: Session, verifying_game: Callable[..., GameSession]
) -> None:
    game = verifying_game()
    photographed = lock(game, db)
    quit_game(game.student_id, game.id, db)

    assert refused(lambda: finish(game, True, db, photographed)) == ErrorCode.SESSION_NOT_ACTIVE
    assert (photographed.attempts_used, ledger_rows(photographed, db)) == (0, 0)
