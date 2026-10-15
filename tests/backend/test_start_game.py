"""start_game (A7): a new game with 5 drawn locations, or the right error."""

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.content import interface as content
from app.errors import AppError, ErrorCode
from app.game import start
from app.game.start import start_game
from app.models.accounts import User
from app.models.engagement import Class, ClassEnrollment
from app.models.enums import (
    Difficulty,
    EnrollmentEndReason,
    Language,
    LocationStatus,
    SessionEndReason,
    SessionState,
    SkipReason,
    UserRole,
)
from app.models.game import GameSession, SessionLocation
from app.services.audio import clue_audio_url


def _locations(game_id: UUID, db: Session) -> list[SessionLocation]:
    return list(
        db.scalars(
            select(SessionLocation)
            .where(SessionLocation.session_id == game_id)
            .order_by(SessionLocation.position)
        )
    )


def _game_count(user: User, db: Session) -> int:
    return db.scalar(select(func.count()).where(GameSession.student_id == user.id)) or 0


def test_practice_game_starts_with_five_locations(
    db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user()

    started = start_game(student, Difficulty.ADVANCING, db, language=Language.KO)

    # Test case 1: only the session id, state, position and first clue URL
    assert set(asdict(started)) == {"session_id", "state", "position", "clue_audio_url"}
    assert (started.state, started.position) == (SessionState.ACTIVE, 1)
    game = db.get_one(GameSession, started.session_id)
    assert (game.student_id, game.class_id) == (student.id, None)
    assert (game.target_language, game.difficulty) == (Language.KO, Difficulty.ADVANCING)
    assert (game.state, game.current_position) == (SessionState.ACTIVE, 1)

    locations = _locations(game.id, db)
    assert [location.position for location in locations] == [1, 2, 3, 4, 5]
    assert len({location.location_id for location in locations}) == 5
    first, *rest = locations
    assert first.status == LocationStatus.NAVIGATING and first.started_at is not None
    assert all(loc.status == LocationStatus.PENDING and loc.started_at is None for loc in rest)
    assert started.clue_audio_url == clue_audio_url(first.audio_asset_id, db)

    snapshot = content.location_snapshot(db, first.location_id, Language.KO, Difficulty.ADVANCING)
    assert first.clue_id == snapshot.clue_id
    assert first.accepted_answers_snapshot == list(snapshot.accepted_answers)
    assert first.gps_points_snapshot == [{"lat": p.lat, "lng": p.lng} for p in snapshot.gps_points]
    assert first.radius_snapshot == snapshot.radius_meters


def test_class_game_uses_the_class_language(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    student, school_class = make_user(), make_class(target_language=Language.FR)
    make_enrollment(student, school_class)

    started = start_game(student, Difficulty.BASIC, db, class_id=school_class.id)

    game = db.get_one(GameSession, started.session_id)
    assert (game.class_id, game.target_language) == (school_class.id, Language.FR)


def test_staff_can_play_practice(db: Session, make_user: Callable[..., User]) -> None:
    started = start_game(make_user(role=UserRole.STAFF), Difficulty.BASIC, db, language=Language.JA)
    assert started.state == SessionState.ACTIVE


def test_not_enough_content(
    db: Session, make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    four = frozenset(list(content.eligible_location_ids(db, Language.JA, Difficulty.BASIC))[:4])
    monkeypatch.setattr(content, "eligible_location_ids", lambda *_: four)

    student = make_user()

    # Test case 2
    with pytest.raises(AppError) as raised:
        start_game(student, Difficulty.BASIC, db, language=Language.JA)
    assert raised.value.code == ErrorCode.NOT_ENOUGH_CONTENT
    assert _game_count(student, db) == 0


@pytest.mark.parametrize("eligible_count", [5, 6])
def test_location_that_stops_being_eligible_is_replaced(
    db: Session,
    make_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
    eligible_count: int,
) -> None:
    listed = list(content.eligible_location_ids(db, Language.JA, Difficulty.BASIC))
    listed = listed[:eligible_count]
    gone = listed[0]
    real_snapshot = content.location_snapshot

    def snapshot(db: Session, location_id: UUID, *args: Any) -> content.LocationSnapshot:
        if location_id == gone:  # e.g. deactivated after the eligible list was read
            raise content.LocationNotEligibleError(location_id)
        return real_snapshot(db, location_id, *args)

    monkeypatch.setattr(content, "eligible_location_ids", lambda *_: frozenset(listed))
    monkeypatch.setattr(content, "location_snapshot", snapshot)
    student = make_user()

    if eligible_count == 5:  # only 4 left
        with pytest.raises(AppError) as raised:
            start_game(student, Difficulty.BASIC, db, language=Language.JA)
        assert raised.value.code == ErrorCode.NOT_ENOUGH_CONTENT
        return
    started = start_game(student, Difficulty.BASIC, db, language=Language.JA)
    drawn = {location.location_id for location in _locations(started.session_id, db)}
    assert drawn == set(listed[1:])


@pytest.mark.parametrize("state", [SessionState.ACTIVE, SessionState.PAUSED])
def test_open_game_blocks_a_new_one(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    state: SessionState,
) -> None:
    student = make_user()
    paused_at = datetime.now(UTC) if state == SessionState.PAUSED else None
    open_game = make_game_session(student, state=state, paused_at=paused_at)

    # Test case 3, also for a game in another mode
    with pytest.raises(AppError) as raised:
        start_game(student, Difficulty.CHALLENGING, db, language=Language.ES)
    assert raised.value.code == ErrorCode.SESSION_ALREADY_OPEN
    assert raised.value.details == [{"session_id": str(open_game.id)}]
    assert _game_count(student, db) == 1


def test_idle_open_game_expires_instead(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    idle_since = datetime.now(UTC) - timedelta(hours=24, minutes=1)
    idle = make_game_session(
        student, state=SessionState.PAUSED, paused_at=idle_since, last_activity_at=idle_since
    )
    done = make_session_location(
        idle,
        1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=idle_since,
    )
    unplayed = make_session_location(idle, 2)

    start_game(student, Difficulty.BASIC, db, language=Language.JA)

    db.refresh(idle)
    assert (idle.state, idle.end_reason) == (SessionState.ABANDONED, SessionEndReason.EXPIRED)
    assert idle.paused_at is None and idle.last_activity_at == idle_since
    db.refresh(done)
    db.refresh(unplayed)
    assert done.status == LocationStatus.COMPLETED
    assert (unplayed.status, unplayed.skip_reason) == (
        LocationStatus.SKIPPED,
        SkipReason.SESSION_ENDED,
    )


def test_lost_race_reports_the_other_game(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two starts at once: both pass the check, and the unique index stops the second.
    student = make_user()
    real_check = start._check_no_open_game
    calls: list[UUID] = []

    def check_after_the_first(student_id: UUID, db: Session) -> None:
        calls.append(student_id)
        if len(calls) > 1:
            real_check(student_id, db)

    winner = make_game_session(student)
    monkeypatch.setattr(start, "_check_no_open_game", check_after_the_first)

    with pytest.raises(AppError) as raised:
        start_game(student, Difficulty.BASIC, db, language=Language.JA)
    assert raised.value.code == ErrorCode.SESSION_ALREADY_OPEN
    assert raised.value.details == [{"session_id": str(winner.id)}]
    assert db.scalar(text("SELECT 1")) == 1  # the caller's transaction still works


def test_lost_race_to_a_game_that_already_ended(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The recheck finds no open game, so there is no id to report, but it is still a conflict.
    student = make_user()
    make_game_session(student)
    monkeypatch.setattr(start, "_check_no_open_game", lambda *_: None)

    with pytest.raises(AppError) as raised:
        start_game(student, Difficulty.BASIC, db, language=Language.JA)
    assert raised.value.code == ErrorCode.SESSION_ALREADY_OPEN
    assert raised.value.details == []


def test_archived_class(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    student, school_class = make_user(), make_class(archived_at=datetime.now(UTC))
    make_enrollment(student, school_class)

    with pytest.raises(AppError) as raised:
        start_game(student, Difficulty.BASIC, db, class_id=school_class.id)
    assert raised.value.code == ErrorCode.CLASS_ARCHIVED


@pytest.mark.parametrize("case", ["unknown", "not_enrolled", "left", "staff"])
def test_class_the_user_is_not_in(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    case: str,
) -> None:
    school_class = make_class()
    user = make_user(role=UserRole.STAFF) if case == "staff" else make_user()
    if case == "left":
        make_enrollment(
            user,
            school_class,
            is_active=False,
            end_reason=EnrollmentEndReason.LEFT,
            left_at=datetime.now(UTC),
        )
    class_id = uuid4() if case == "unknown" else school_class.id

    with pytest.raises(AppError) as raised:
        start_game(user, Difficulty.BASIC, db, class_id=class_id)
    assert raised.value.code == ErrorCode.NOT_FOUND
    assert _game_count(user, db) == 0


def test_class_or_language_not_both(db: Session, make_user: Callable[..., User]) -> None:
    with pytest.raises(ValueError):
        start_game(make_user(), Difficulty.BASIC, db, class_id=uuid4(), language=Language.JA)


def test_admins_do_not_play(db: Session, make_user: Callable[..., User]) -> None:
    with pytest.raises(AppError) as raised:
        start_game(make_user(role=UserRole.ADMIN), Difficulty.BASIC, db, language=Language.JA)
    assert raised.value.code == ErrorCode.FORBIDDEN_ROLE


def test_doesnt_commit(
    db: Session, make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    # spied directly: releasing the savepoint fires the same session events as a commit
    calls: list[str] = []
    monkeypatch.setattr(db, "commit", lambda: calls.append("commit"))
    monkeypatch.setattr(db, "rollback", lambda: calls.append("rollback"))

    start_game(make_user(), Difficulty.BASIC, db, language=Language.JA)

    assert calls == []
