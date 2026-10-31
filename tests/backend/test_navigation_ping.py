"""POST /games/{id}/navigation/ping (A19): tracking stored on the current location."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.game.navigation import Ping, PingOutcome, Trend, process_ping
from app.models.accounts import User
from app.models.enums import FeedbackCode, Language, LocationStatus, SessionState
from app.models.game import GameSession
from app.services.audio import UploadedAudioService

NOW = datetime(2026, 10, 29, 20, 0, tzinfo=UTC)
LAT, LNG = 49.9394, -119.3948  # the entrance make_session_location uses, radius 30 m
M = 1 / 111_195  # about one metre of latitude


def north(metres: float, accuracy: float = 5) -> Ping:
    return Ping(LAT + metres * M, LNG, accuracy)


@pytest.fixture
def playing(navigating_game: Callable[..., Any]) -> Callable[..., Any]:
    """An active game navigating to its first location, which started a minute ago."""
    return partial(navigating_game, NOW, "app.game.navigation")


def ping(game: GameSession, at: Ping, db: Session) -> PingOutcome:
    return process_ping(game.student_id, game.id, at, db)


def test_the_first_ping_starts_navigation(db: Session, playing: Callable[..., Any]) -> None:
    game, row = playing()

    outcome = ping(game, north(500), db)

    assert (outcome.feedback, outcome.speak, outcome.trend, outcome.can_confirm) == (
        FeedbackCode.NAV_START,
        True,
        None,
        False,
    )
    expected = UploadedAudioService().feedback_audio_url(FeedbackCode.NAV_START, Language.JA, db)
    assert urlsplit(outcome.feedback_audio_url).path == urlsplit(expected).path
    assert row.last_ping_at == row.ref_at == row.last_spoken_at == NOW
    assert (round(row.last_distance_m), row.ref_point_index, row.last_feedback_code) == (
        500,
        0,
        FeedbackCode.NAV_START,
    )
    assert game.last_activity_at == NOW
    assert outcome.gps_override_available_at == NOW + timedelta(minutes=2)


def test_a_trend_against_the_stored_reference(db: Session, playing: Callable[..., Any]) -> None:
    earlier = NOW - timedelta(seconds=30)
    game, row = playing(
        last_lat=LAT + 500 * M,
        last_lng=LNG,
        last_accuracy_m=5,
        last_distance_m=500,
        last_ping_at=NOW - timedelta(seconds=5),
        ref_point_index=0,
        ref_distance_m=500,
        ref_at=earlier,
        last_feedback_code=FeedbackCode.NAV_START,
        last_spoken_at=earlier,
    )

    outcome = ping(game, north(450), db)

    assert (outcome.trend, outcome.feedback, outcome.speak) == (
        Trend.CLOSER,
        FeedbackCode.NAV_GETTING_CLOSER,
        True,
    )
    assert (row.ref_at, round(row.ref_distance_m)) == (NOW, 450)


def test_within_the_radius_the_student_can_confirm(
    db: Session, playing: Callable[..., Any]
) -> None:
    game, row = playing()

    outcome = ping(game, north(10), db)

    assert (outcome.feedback, outcome.can_confirm) == (FeedbackCode.NAV_ARRIVED, True)
    assert row.status == LocationStatus.NAVIGATING


def test_weak_pings_move_the_override_time_and_a_good_one_resets_it(
    db: Session, playing: Callable[..., Any], freeze_clock: Callable[..., None]
) -> None:
    game, row = playing()
    for second in (0, 5, 10):
        freeze_clock(NOW + timedelta(seconds=second), "app.game.navigation")
        outcome = ping(game, north(500, accuracy=80), db)

    assert (row.weak_pings_in_row, outcome.feedback) == (3, FeedbackCode.NAV_GPS_WEAK)
    assert outcome.gps_override_available_at == NOW + timedelta(seconds=10)
    # kept, so the next good fix isn't compared with it for speed
    assert row.last_accuracy_m == 80

    freeze_clock(NOW + timedelta(seconds=15), "app.game.navigation")
    outcome = ping(game, north(500), db)

    assert row.weak_pings_in_row == 0
    assert outcome.gps_override_available_at == NOW + timedelta(minutes=2)


def test_a_jump_is_flagged_once(db: Session, playing: Callable[..., Any]) -> None:
    game, row = playing(
        last_lat=LAT + 500 * M,
        last_lng=LNG,
        last_accuracy_m=5,
        last_distance_m=500,
        last_ping_at=NOW - timedelta(seconds=5),
    )
    row.flags = ["something_else"]

    ping(game, north(300), db)
    row.last_ping_at = NOW - timedelta(seconds=5)
    ping(game, north(100), db)

    assert row.flags == ["something_else", "speed_anomaly"]


@pytest.mark.parametrize(("ago", "allowed"), [(1.9, False), (2, True)])
def test_pings_are_rate_limited(
    db: Session, playing: Callable[..., Any], ago: float, allowed: bool
) -> None:
    game, row = playing(last_ping_at=NOW - timedelta(seconds=ago))

    if allowed:
        ping(game, north(500), db)
        assert row.last_ping_at == NOW
        return
    with pytest.raises(AppError) as error:
        ping(game, north(500), db)
    assert error.value.code == ErrorCode.RATE_LIMITED
    assert game.last_activity_at == NOW - timedelta(hours=1)


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"game": {"state": SessionState.PAUSED, "paused_at": NOW}}, ErrorCode.SESSION_NOT_ACTIVE),
        (
            {"game": {"state": SessionState.COMPLETED, "completed_at": NOW}},
            ErrorCode.SESSION_NOT_ACTIVE,
        ),
        ({"status": LocationStatus.VERIFYING}, ErrorCode.INVALID_STAGE),
    ],
)
def test_only_stage_1_of_an_active_game(
    db: Session, playing: Callable[..., Any], fields: dict[str, Any], code: ErrorCode
) -> None:
    game, row = playing(**fields)
    db.flush()

    with pytest.raises(AppError) as error:
        ping(game, north(500), db)

    assert error.value.code == code
    assert row.last_ping_at is None


def test_the_route(
    db: Session,
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    playing: Callable[..., Any],
    transaction_calls: list[str],
    assert_error: Callable[..., None],
) -> None:
    game, row = playing()
    student = db.get(User, game.student_id)
    assert student is not None
    url = f"/api/v1/games/{game.id}/navigation/ping"
    body = {"lat": LAT + 500 * M, "lng": LNG, "accuracy_m": 5}
    transaction_calls.clear()

    response = client.post(url, json=body, headers=auth_headers(student))

    assert response.status_code == 200, response.text
    assert set(response.json()) == {
        "trend",
        "can_confirm",
        "speak",
        "feedback",
        "gps_override_available_at",
    }
    assert response.json()["feedback"]["code"] == FeedbackCode.NAV_START.value
    assert "lat" not in response.text
    assert transaction_calls.count("commit") == 1
    assert_error(
        client.post(url, json=body, headers=auth_headers(student)), 429, ErrorCode.RATE_LIMITED
    )
    for bad in ({"lat": 91}, {"lng": -181}, {"accuracy_m": -1}):
        response = client.post(url, json=body | bad, headers=auth_headers(student))
        assert response.status_code == 422, bad
    # Infinity is not a JSON number the client will encode, so send it as text
    infinity = f'{{"lat": {body["lat"]}, "lng": {body["lng"]}, "accuracy_m": Infinity}}'
    response = client.post(
        url,
        content=infinity,
        headers=auth_headers(student) | {"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    other = auth_headers(make_user())
    assert_error(client.post(url, json=body, headers=other), 404, ErrorCode.NOT_FOUND)
    admin = auth_headers(make_user(role="admin"))
    assert_error(client.post(url, json=body, headers=admin), 403, ErrorCode.FORBIDDEN_ROLE)
    assert_error(client.post(url, json=body), 401, ErrorCode.NOT_AUTHENTICATED)
