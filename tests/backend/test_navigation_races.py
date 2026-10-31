"""Concurrent navigation on real Postgres (A19). Each action has its own session.

The jobs start together (run_together in conftest) and keep their transactions open for HOLD s,
so the row locks overlap. A job given HOLD as its delay starts after the first one holds the lock.
"""

from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session

from app.content import interface as content
from app.errors import AppError, ErrorCode
from app.game.arrival import Arrival, confirm_arrival
from app.game.control import pause_game, quit_game
from app.game.current_location import CurrentLocation, get_current_location
from app.game.navigation import Ping, PingOutcome, process_ping
from app.game.shuffle import shuffle_game
from app.models.accounts import User
from app.models.enums import (
    ArrivalOverride,
    Difficulty,
    FeedbackCode,
    Language,
    LocationStatus,
    SkipReason,
    UserRole,
)
from app.models.game import GameSession, SessionLocation
from app.services.audio import UploadedAudioService

LAT, LNG = 49.9394, -119.3948
FAR_LAT = 50.0  # well outside every stub entrance
HOLD = 0.3
GPS = ArrivalOverride.GPS_UNAVAILABLE


def _start(engine: Engine, rows: list[dict[str, Any]]) -> tuple[UUID, UUID]:
    with Session(engine) as session:
        user = User(
            email=f"{uuid4().hex[:12]}@example.com",
            username=f"user_{uuid4().hex[:8]}",
            role=UserRole.STUDENT,
        )
        session.add(user)
        session.flush()
        game = GameSession(
            student_id=user.id,
            target_language=Language.JA,
            difficulty=Difficulty.BASIC,
            last_activity_at=datetime.now(UTC),
            current_position=1,
        )
        session.add(game)
        session.flush()
        audio = UploadedAudioService()
        for position, fields in enumerate(rows, start=1):
            values: dict[str, Any] = {
                "session_id": game.id,
                "location_id": uuid4(),
                "position": position,
                "clue_id": uuid4(),
                "audio_asset_id": audio.clue_audio_asset_id(uuid4(), session),
                "accepted_answers_snapshot": ["SCI333"],
                "gps_points_snapshot": [{"lat": LAT, "lng": LNG}],
                "radius_snapshot": 30,
                "status": LocationStatus.PENDING,
            }
            session.add(SessionLocation(**(values | fields)))
        session.commit()
        return user.id, game.id


def _cleanup(engine: Engine, user_id: UUID) -> None:
    with Session(engine) as session:
        session.execute(text("SET lock_timeout = '10s'"))
        game_ids = select(GameSession.id).where(GameSession.student_id == user_id)
        session.execute(delete(SessionLocation).where(SessionLocation.session_id.in_(game_ids)))
        session.execute(delete(GameSession).where(GameSession.student_id == user_id))
        session.execute(delete(User).where(User.id == user_id))
        session.commit()


@contextmanager
def _playing(engine: Engine, rows: list[dict[str, Any]]) -> Any:
    user_id, game_id = _start(engine, rows)
    try:
        yield user_id, game_id
    finally:
        _cleanup(engine, user_id)


def _navigating(**fields: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "status": LocationStatus.NAVIGATING,
        "started_at": now - timedelta(minutes=5),
        "last_ping_at": now - timedelta(seconds=5),
        "last_distance_m": 10,
        "last_accuracy_m": 5,
        "last_lat": LAT,
        "last_lng": LNG,
    }
    return values | fields


def _live(session: Session, game_id: UUID, position: int) -> SessionLocation:
    row = session.scalar(
        select(SessionLocation).where(
            SessionLocation.session_id == game_id,
            SessionLocation.position == position,
            SessionLocation.skip_reason.is_distinct_from(SkipReason.SHUFFLED),
        )
    )
    assert row is not None
    return row


def _body(arrival: Arrival) -> tuple[Any, ...]:
    location = arrival.location
    return (
        location.state,
        location.position,
        location.stage,
        location.attempts_used,
        location.attempts_left,
        location.gps_override_available_at,
        urlsplit(location.clue_audio_url).path,
        arrival.feedback,
        urlsplit(arrival.feedback_audio_url).path,
    )


def test_two_confirms_agree_and_record_one_override(
    test_engine: Engine, run_together: Callable[..., list[Any]]
) -> None:
    with _playing(test_engine, [_navigating()]) as (student_id, game_id):

        def confirm(session: Session) -> Arrival:
            return confirm_arrival(student_id, game_id, GPS, None, session)

        first, second = run_together(confirm, confirm, hold=HOLD)
        assert isinstance(first, Arrival) and isinstance(second, Arrival)
        assert _body(first) == _body(second)
        with Session(test_engine) as session:
            row = _live(session, game_id, 1)
            assert (row.status, row.flags) == (LocationStatus.VERIFYING, ["gps_override"])


def test_a_ping_after_confirm_is_the_wrong_stage(
    test_engine: Engine, run_together: Callable[..., list[Any]]
) -> None:
    with _playing(test_engine, [_navigating()]) as (student_id, game_id):

        def confirm(session: Session) -> Arrival:
            return confirm_arrival(student_id, game_id, None, None, session)

        def ping(session: Session) -> PingOutcome:
            return process_ping(student_id, game_id, Ping(LAT, LNG, 5), session)

        confirmed, pinged = run_together(confirm, (ping, HOLD), hold=HOLD)

    assert isinstance(confirmed, Arrival)
    assert isinstance(pinged, AppError)
    assert pinged.code == ErrorCode.INVALID_STAGE


def test_two_pings_inside_two_seconds_one_is_rate_limited(
    test_engine: Engine, run_together: Callable[..., list[Any]]
) -> None:
    with _playing(test_engine, [_navigating(last_ping_at=None)]) as (student_id, game_id):

        def ping(session: Session) -> PingOutcome:
            return process_ping(student_id, game_id, Ping(LAT, LNG, 5), session)

        results = run_together(ping, ping, hold=HOLD)

    assert sum(isinstance(result, PingOutcome) for result in results) == 1
    limited = [result for result in results if isinstance(result, AppError)]
    assert [result.code for result in limited] == [ErrorCode.RATE_LIMITED]


def test_confirm_after_pause_is_not_active(
    test_engine: Engine, run_together: Callable[..., list[Any]]
) -> None:
    with _playing(test_engine, [_navigating()]) as (student_id, game_id):

        def pause(session: Session) -> Any:
            return pause_game(student_id, game_id, session)

        def confirm(session: Session) -> Arrival:
            return confirm_arrival(student_id, game_id, None, None, session)

        _paused, confirmed = run_together(pause, (confirm, HOLD), hold=HOLD)

    assert isinstance(confirmed, AppError)
    assert confirmed.code == ErrorCode.SESSION_NOT_ACTIVE


@pytest.mark.parametrize("follow", ["confirm", "ping"])
def test_quit_then_a_play_action_is_not_active(
    test_engine: Engine, run_together: Callable[..., list[Any]], follow: str
) -> None:
    with _playing(test_engine, [_navigating()]) as (student_id, game_id):

        def quit(session: Session) -> Any:
            return quit_game(student_id, game_id, session)

        def confirm(session: Session) -> Arrival:
            return confirm_arrival(student_id, game_id, None, None, session)

        def ping(session: Session) -> PingOutcome:
            return process_ping(student_id, game_id, Ping(LAT, LNG, 5), session)

        follow_up = confirm if follow == "confirm" else ping
        _ended, followed = run_together(quit, (follow_up, HOLD), hold=HOLD)

    assert isinstance(followed, AppError)
    assert followed.code == ErrorCode.SESSION_NOT_ACTIVE


def test_a_ping_after_a_shuffle_starts_the_new_row(
    test_engine: Engine, run_together: Callable[..., list[Any]]
) -> None:
    with _playing(test_engine, [_navigating(), {"status": LocationStatus.PENDING}]) as ids:
        student_id, game_id = ids

        def shuffle(session: Session) -> Any:
            return shuffle_game(student_id, game_id, 0, session)

        def ping(session: Session) -> PingOutcome:
            return process_ping(student_id, game_id, Ping(FAR_LAT, LNG, 5), session)

        _shuffled, pinged = run_together(shuffle, (ping, HOLD), hold=HOLD)
        assert isinstance(pinged, PingOutcome)
        assert pinged.feedback == FeedbackCode.NAV_START
        with Session(test_engine) as session:
            assert _live(session, game_id, 1).last_feedback_code == FeedbackCode.NAV_START


def test_confirm_after_a_shuffle_is_a_stale_position(
    test_engine: Engine, run_together: Callable[..., list[Any]]
) -> None:
    with _playing(test_engine, [_navigating(), {"status": LocationStatus.PENDING}]) as ids:
        student_id, game_id = ids

        def shuffle(session: Session) -> Any:
            return shuffle_game(student_id, game_id, 0, session)

        def confirm(session: Session) -> Arrival:
            return confirm_arrival(student_id, game_id, None, 1, session)

        _shuffled, confirmed = run_together(shuffle, (confirm, HOLD), hold=HOLD)

    assert isinstance(confirmed, AppError)
    assert confirmed.code == ErrorCode.STALE_POSITION


def test_two_current_location_calls_skip_invalid_rows_once(
    test_engine: Engine, run_together: Callable[..., list[Any]]
) -> None:
    with Session(test_engine) as session:
        active = sorted(content.eligible_location_ids(Language.JA, Difficulty.BASIC, session))
    rows = [
        _navigating(location_id=uuid4()),
        {"status": LocationStatus.PENDING, "location_id": uuid4()},
        {"status": LocationStatus.PENDING, "location_id": active[0]},
    ]
    with _playing(test_engine, rows) as (student_id, game_id):

        def current(session: Session) -> CurrentLocation | Any:
            return get_current_location(student_id, game_id, session)

        first, second = run_together(current, current, hold=HOLD)
        assert isinstance(first, CurrentLocation) and isinstance(second, CurrentLocation)
        assert (first.position, second.position) == (3, 3)
        with Session(test_engine) as session:
            stored = list(
                session.scalars(
                    select(SessionLocation)
                    .where(SessionLocation.session_id == game_id)
                    .order_by(SessionLocation.position)
                )
            )
        assert [(row.position, row.status, row.skip_reason) for row in stored] == [
            (1, LocationStatus.SKIPPED, SkipReason.DEACTIVATED),
            (2, LocationStatus.SKIPPED, SkipReason.DEACTIVATED),
            (3, LocationStatus.NAVIGATING, None),
        ]
