"""draw_locations (A11): carry-over after a quit, then uncompleted locations first."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content import interface as content
from app.game.draw import draw_locations
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
    return sorted(content.eligible_location_ids(db, JA, BASIC))


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

    assert set(eligible[:4]) <= set(drawn)  # the only 4 not completed yet
    # completions at another difficulty don't count
    other = draw_locations(student.id, None, JA, Difficulty.ADVANCING, db)
    assert len(other) == 5


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

    assert drawn[:2] == eligible[1:3]  # in their old order, before the new draw


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


@pytest.mark.parametrize("case", ["other_mode", "other_difficulty", "reset", "played_since"])
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
    earlier = datetime.now(UTC) - timedelta(seconds=10)
    if case == "other_mode":
        quit_game(student, class_id=make_class().id)  # a class game, then Practice
    elif case == "other_difficulty":
        quit_game(student, difficulty=Difficulty.CHALLENGING)
    elif case == "reset":
        game = quit_game(student)
        game.end_reason = SessionEndReason.RESET
    else:  # a later game in the same mode replayed them already
        quit_game(student, at=earlier)
        now = datetime.now(UTC)
        make_game_session(
            student, state=SessionState.COMPLETED, completed_at=now, last_activity_at=now
        )
    db.flush()
    _only_carry_over_could_pick(monkeypatch, eligible)

    drawn = _ids(draw_locations(student.id, None, JA, BASIC, db))

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


def _only_carry_over_could_pick(monkeypatch: pytest.MonkeyPatch, eligible: list[UUID]) -> None:
    """Make a random draw always miss the quit game's unplayed locations (eligible[1:3]), so
    they can only appear through carry-over."""
    monkeypatch.setattr(
        "app.game.draw.random.shuffle",
        lambda ids: ids.sort(key=lambda location_id: location_id in eligible[1:3]),
    )
