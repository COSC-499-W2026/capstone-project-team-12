"""draw_locations (A11): carry-over after a quit, then uncompleted locations first."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content import interface as content
from app.errors import AppError, ErrorCode
from app.game.draw import _completed, draw_locations, draw_replacements
from app.models.accounts import User
from app.models.engagement import Class
from app.models.enums import (
    Difficulty,
    Language,
    LocationStatus,
    SessionEndReason,
    SessionState,
    SkipReason,
)
from app.models.game import GameSession, SessionLocation

JA, BASIC = Language.JA, Difficulty.BASIC


@pytest.fixture
def eligible(db: Session) -> list[UUID]:
    return sorted(content.eligible_location_ids(JA, BASIC, db))


@pytest.fixture
def quit_game(
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    eligible: list[UUID],
) -> Callable[..., GameSession]:
    """A game quit at `at` (now by default): position 1 completed, positions 2 and 3 unplayed."""

    def make(student: User, at: datetime | None = None, **fields: object) -> GameSession:
        at = at or datetime.now(UTC)
        game = make_game_session(
            student,
            state=SessionState.ABANDONED,
            end_reason=SessionEndReason.QUIT,
            last_activity_at=at,
            **fields,
        )
        make_session_location(
            game,
            1,
            location_id=eligible[0],
            status=LocationStatus.COMPLETED,
            points_awarded=100,
            completed_at=at,
        )
        for position in (2, 3):
            make_session_location(
                game,
                position,
                location_id=eligible[position - 1],
                status=LocationStatus.SKIPPED,
                skip_reason=SkipReason.SESSION_ENDED,
            )
        return game

    return make


def _ids(snapshots: list[content.LocationSnapshot]) -> list[UUID]:
    return [snapshot.location_id for snapshot in snapshots]


def test_five_different_eligible_locations(
    db: Session, make_user: Callable[..., User], eligible: list[UUID]
) -> None:
    drawn = _ids(draw_locations(make_user().id, None, JA, BASIC, db))
    assert len(set(drawn)) == 5 and set(drawn) <= set(eligible)


def test_uncompleted_locations_come_first(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    eligible: list[UUID],
) -> None:
    student = make_user()
    now = datetime.now(UTC)
    done = make_game_session(student, state=SessionState.COMPLETED, completed_at=now)
    for position, location_id in enumerate(eligible[4:], start=1):
        make_session_location(
            done,
            position,
            location_id=location_id,
            status=LocationStatus.COMPLETED,
            points_awarded=100,
            completed_at=now,
        )

    drawn = _ids(draw_locations(student.id, None, JA, BASIC, db))

    # the only 4 not completed yet
    assert set(eligible[:4]) <= set(drawn)
    # completions only count in their own language and difficulty
    assert _completed(student.id, JA, BASIC, db) == set(eligible[4:])
    assert _completed(student.id, JA, Difficulty.ADVANCING, db) == set()
    assert _completed(student.id, Language.KO, BASIC, db) == set()


def test_not_enough_eligible_locations(
    db: Session,
    make_user: Callable[..., User],
    eligible: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(content, "eligible_location_ids", lambda *_: frozenset(eligible[:4]))

    with pytest.raises(AppError) as raised:
        draw_locations(make_user().id, None, JA, BASIC, db)
    assert raised.value.code == ErrorCode.NOT_ENOUGH_CONTENT


@pytest.mark.parametrize("failing", [1, 2])
def test_snapshot_failures_are_skipped(
    db: Session,
    make_user: Callable[..., User],
    eligible: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
    failing: int,
) -> None:
    # 6 eligible, and the snapshot fails for the first `failing` of them (e.g. deactivated
    # after the eligible list was read): 1 failure still leaves 5, 2 failures leave only 4.
    six, gone = eligible[:6], set(eligible[:failing])
    real_snapshot = content.location_snapshot

    def snapshot(
        location_id: UUID, language: Language, difficulty: Difficulty, db: Session
    ) -> content.LocationSnapshot:
        if location_id in gone:
            raise content.LocationNotEligibleError(location_id)
        return real_snapshot(location_id, language, difficulty, db)

    monkeypatch.setattr(content, "eligible_location_ids", lambda *_: frozenset(six))
    monkeypatch.setattr(content, "location_snapshot", snapshot)
    # try the failing ones first, so they are always reached
    monkeypatch.setattr("app.game.draw.random.shuffle", lambda ids: ids.sort(key=six.index))

    if failing == 2:
        with pytest.raises(AppError) as raised:
            draw_locations(make_user().id, None, JA, BASIC, db)
        assert raised.value.code == ErrorCode.NOT_ENOUGH_CONTENT
        return
    drawn = _ids(draw_locations(make_user().id, None, JA, BASIC, db))
    assert drawn == six[1:]


@pytest.mark.parametrize("in_class", [False, True])
def test_quit_today_carries_unplayed_locations_over(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    quit_game: Callable[..., GameSession],
    eligible: list[UUID],
    in_class: bool,
) -> None:
    # Practice, or the same class as the game that was quit
    student = make_user()
    class_id = make_class().id if in_class else None
    quit_game(student, class_id=class_id)

    drawn = _ids(draw_locations(student.id, class_id, JA, BASIC, db))

    # in their old order, before the new draw
    assert drawn[:2] == eligible[1:3]


def test_carry_over_can_be_turned_off(
    db: Session,
    make_user: Callable[..., User],
    quit_game: Callable[..., GameSession],
    eligible: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # what a reset passes, so the last quit game's locations don't come back
    student = make_user()
    quit_game(student)
    _only_carry_over_could_pick(monkeypatch, eligible)

    drawn = _ids(draw_locations(student.id, None, JA, BASIC, db, carry_over=False))

    assert not set(drawn) & set(eligible[1:3])


def test_no_carry_over_from_yesterday(
    db: Session,
    make_user: Callable[..., User],
    quit_game: Callable[..., GameSession],
    eligible: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    student = make_user()
    zone = ZoneInfo(get_settings().app_timezone)
    today_start = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    quit_game(student, at=today_start - timedelta(minutes=1))
    _only_carry_over_could_pick(monkeypatch, eligible)

    drawn = _ids(draw_locations(student.id, None, JA, BASIC, db))

    assert not set(drawn) & set(eligible[1:3])


@pytest.mark.parametrize(
    "case",
    [
        "class_then_practice",
        "practice_then_class",
        "other_class",
        "other_difficulty",
        "reset",
        "expired",
        "class_closed",
        "played_since",
    ],
)
def test_no_carry_over(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_game_session: Callable[..., GameSession],
    quit_game: Callable[..., GameSession],
    eligible: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    student = make_user()
    # the mode of the new game; None is Practice
    draw_class_id = None
    if case == "class_then_practice":
        quit_game(student, class_id=make_class().id)
    elif case == "practice_then_class":
        quit_game(student)
        draw_class_id = make_class().id
    elif case == "other_class":
        quit_game(student, class_id=make_class().id)
        draw_class_id = make_class().id
    elif case == "other_difficulty":
        quit_game(student, difficulty=Difficulty.CHALLENGING)
    elif case in ("reset", "expired", "class_closed"):
        game = quit_game(student)
        game.end_reason = SessionEndReason(case)
    else:
        # a later game in the same mode replayed them already
        quit_game(student, at=datetime.now(UTC) - timedelta(seconds=10))
        now = datetime.now(UTC)
        make_game_session(
            student, state=SessionState.COMPLETED, completed_at=now, last_activity_at=now
        )
    db.flush()
    _only_carry_over_could_pick(monkeypatch, eligible)

    drawn = _ids(draw_locations(student.id, draw_class_id, JA, BASIC, db))

    assert not set(drawn) & set(eligible[1:3])


def test_carried_location_no_longer_eligible_is_left_out(
    db: Session,
    make_user: Callable[..., User],
    quit_game: Callable[..., GameSession],
    eligible: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    student = make_user()
    quit_game(student)
    monkeypatch.setattr(
        content, "eligible_location_ids", lambda *_: frozenset(eligible) - {eligible[1]}
    )

    drawn = _ids(draw_locations(student.id, None, JA, BASIC, db))

    assert drawn[0] == eligible[2] and eligible[1] not in drawn


def test_only_locations_skipped_by_the_quit_are_carried_over(
    db: Session,
    make_user: Callable[..., User],
    quit_game: Callable[..., GameSession],
    eligible: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    student = make_user()
    game = quit_game(student)
    third = db.scalars(
        select(SessionLocation).where(
            SessionLocation.session_id == game.id, SessionLocation.position == 3
        )
    ).one()
    third.skip_reason = SkipReason.DEACTIVATED
    db.flush()
    _only_carry_over_could_pick(monkeypatch, eligible)

    drawn = _ids(draw_locations(student.id, None, JA, BASIC, db))

    assert eligible[1] in drawn and eligible[2] not in drawn


def test_replacements_skip_the_excluded_locations_and_prefer_new_ones(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    eligible: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # a shuffle excludes every location already in its game
    student = make_user()
    done = make_game_session(student, state=SessionState.COMPLETED, completed_at=datetime.now(UTC))
    make_session_location(
        done,
        1,
        location_id=eligible[5],
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )
    monkeypatch.setattr(content, "eligible_location_ids", lambda *_: frozenset(eligible[:7]))

    drawn = _ids(draw_replacements(student.id, JA, BASIC, eligible[:4], 3, db))

    # eligible[4] and [6] are new to the student, so they come before the completed [5]
    assert sorted(drawn[:2]) == [eligible[4], eligible[6]]
    assert drawn[2] == eligible[5]
    with pytest.raises(AppError) as error:
        draw_replacements(student.id, JA, BASIC, eligible[:5], 3, db)
    assert error.value.code == ErrorCode.NOT_ENOUGH_CONTENT


def test_drawing_only_reads(
    db: Session, make_user: Callable[..., User], transaction_calls: list[str]
) -> None:
    student = make_user()
    transaction_calls.clear()  # creating the user flushed

    draw_locations(student.id, None, JA, BASIC, db)

    assert transaction_calls == []


def _only_carry_over_could_pick(monkeypatch: pytest.MonkeyPatch, eligible: list[UUID]) -> None:
    """Make a random draw always miss the quit game's unplayed locations (eligible[1:3]), so
    they can only appear through carry-over."""
    monkeypatch.setattr(
        "app.game.draw.random.shuffle",
        lambda ids: ids.sort(key=lambda location_id: location_id in eligible[1:3]),
    )
