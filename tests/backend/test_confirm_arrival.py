"""POST /games/{id}/navigation/confirm-arrival (A19, TC2): from Stage 1 to Stage 2."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.game.arrival import Arrival, confirm_arrival
from app.game.current_location import Stage
from app.game.navigation import Ping, process_ping
from app.models.accounts import User
from app.models.enums import ArrivalOverride, FeedbackCode, LocationStatus, SessionState
from app.models.game import GameSession

NOW = datetime(2026, 10, 29, 20, 0, tzinfo=UTC)
CLOCK = ("app.game.arrival", "app.game.navigation", "app.game.control", "app.game.status")
LAT, LNG = 49.9394, -119.3948  # the entrance make_session_location uses, radius 30 m
M = 1 / 111_195  # about one metre of latitude
GPS = ArrivalOverride.GPS_UNAVAILABLE
_Split = Callable[[str], tuple[str, int, str]]


@pytest.fixture
def playing(navigating_game: Callable[..., Any]) -> Callable[..., Any]:
    """An active game at its first location, started a minute ago, pinged 5 s ago from 10 m."""
    recent = {"last_ping_at": NOW - timedelta(seconds=5), "last_distance_m": 10}
    return partial(navigating_game, NOW, "app.game.arrival", "app.game.navigation", **recent)


def confirm(
    game: GameSession, db: Session, override: ArrivalOverride | None = None, **kw: Any
) -> Arrival:
    return confirm_arrival(game.student_id, game.id, override, kw.get("position"), db)


def _part(split: _Split, url: str) -> str:
    # full URLs differ: re-signing across a second boundary changes expires and sig
    key, expires, sig = split(url)
    assert expires and sig
    return key


def _same_arrival(left: Arrival, right: Arrival, split: _Split) -> None:
    assert _part(split, left.location.clue_audio_url) == _part(split, right.location.clue_audio_url)
    assert _part(split, left.feedback_audio_url) == _part(split, right.feedback_audio_url)
    assert left.feedback == right.feedback
    assert replace(left.location, clue_audio_url="") == replace(right.location, clue_audio_url="")


def _same_route(left: dict[str, Any], right: dict[str, Any], split: _Split) -> None:
    def bare(body: dict[str, Any]) -> dict[str, Any]:
        heard = [{**i, "audio_url": _part(split, i["audio_url"])} for i in body["feedback"]]
        return {**body, "clue_audio_url": _part(split, body["clue_audio_url"]), "feedback": heard}

    assert bare(left) == bare(right)


def refused(game: GameSession, db: Session, override: ArrivalOverride | None = None) -> ErrorCode:
    with pytest.raises(AppError) as error:
        confirm(game, db, override)
    return error.value.code


def test_a_recent_ping_within_the_radius(db: Session, playing: Callable[..., Any]) -> None:
    game, row = playing()

    arrival = confirm(game, db, position=1)

    assert (row.status, row.arrived_at, row.arrival_override, row.flags) == (
        LocationStatus.VERIFYING,
        NOW,
        None,
        [],
    )
    assert game.last_activity_at == NOW
    assert (arrival.location.stage, arrival.location.gps_override_available_at) == (
        Stage.VERIFICATION,
        None,
    )
    assert arrival.feedback == FeedbackCode.VERIFY_START


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"last_ping_at": NOW - timedelta(seconds=29.9)}, None),
        ({"last_ping_at": NOW - timedelta(seconds=30)}, ErrorCode.STALE_POSITION),
        ({"last_ping_at": None, "last_distance_m": None}, ErrorCode.STALE_POSITION),
        ({"last_distance_m": 30}, None),
        ({"last_distance_m": 30.01}, ErrorCode.NOT_WITHIN_RADIUS),
    ],
)
def test_the_last_ping_must_be_recent_and_within_the_radius(
    db: Session, playing: Callable[..., Any], fields: dict[str, Any], code: ErrorCode | None
) -> None:
    game, row = playing(**fields)

    if code is None:
        confirm(game, db)
        assert row.status == LocationStatus.VERIFYING
        return
    assert refused(game, db) == code
    assert (row.status, game.last_activity_at) == (
        LocationStatus.NAVIGATING,
        NOW - timedelta(hours=1),
    )


@pytest.mark.parametrize(("started", "allowed"), [(179, False), (180, True)])
def test_the_override_after_3_minutes(
    db: Session, playing: Callable[..., Any], started: int, allowed: bool
) -> None:
    # far away and no recent ping: the override doesn't look at either
    game, row = playing(
        started_at=NOW - timedelta(seconds=started), last_ping_at=None, last_distance_m=900
    )

    if not allowed:
        assert refused(game, db, GPS) == ErrorCode.GPS_OVERRIDE_NOT_ALLOWED
        assert row.flags == []
        return
    confirm(game, db, GPS)
    assert (row.status, row.arrival_override, row.flags) == (
        LocationStatus.VERIFYING,
        GPS,
        ["gps_override"],
    )


def test_weak_pings_allow_the_override_until_a_good_one(
    db: Session, playing: Callable[..., Any], freeze_clock: Callable[..., None]
) -> None:
    game, row = playing(started_at=NOW, last_ping_at=None, last_distance_m=None)

    def at(seconds: int) -> None:
        freeze_clock(NOW + timedelta(seconds=seconds), *CLOCK)

    def send(accuracy: float) -> None:
        process_ping(game.student_id, game.id, Ping(LAT + 500 * M, LNG, accuracy), db)

    for second in (0, 5):
        at(second)
        send(80)
    assert refused(game, db, GPS) == ErrorCode.GPS_OVERRIDE_NOT_ALLOWED
    at(10)
    send(80)
    at(12)
    savepoint = db.begin_nested()
    confirm(game, db, GPS)
    assert row.status == LocationStatus.VERIFYING
    savepoint.rollback()  # back to Stage 1, to see a good ping take it away

    at(15)
    send(5)
    assert refused(game, db, GPS) == ErrorCode.GPS_OVERRIDE_NOT_ALLOWED
    at(180)
    confirm(game, db, GPS)
    assert row.arrival_override == GPS


def test_a_repeat_gives_the_same_answer_and_changes_nothing(
    db: Session,
    playing: Callable[..., Any],
    freeze_clock: Callable[..., None],
    split_media_url: _Split,
) -> None:
    game, row = playing(started_at=NOW - timedelta(minutes=5))
    first = confirm(game, db, GPS)
    freeze_clock(NOW + timedelta(minutes=1), *CLOCK)

    # the ping is old by now and the override wouldn't be checked again either
    again = confirm(game, db, GPS, position=1)

    _same_arrival(again, first, split_media_url)
    assert (row.arrived_at, row.flags, game.last_activity_at) == (NOW, ["gps_override"], NOW)


@pytest.mark.parametrize(
    ("fields", "position", "code"),
    [
        ({}, 2, ErrorCode.STATE_CHANGED),
        (
            {"game": {"state": SessionState.PAUSED, "paused_at": NOW}},
            None,
            ErrorCode.SESSION_NOT_ACTIVE,
        ),
        (
            {"status": LocationStatus.COMPLETED, "completed_at": NOW, "points_awarded": 100},
            None,
            ErrorCode.INVALID_STAGE,
        ),
    ],
)
def test_refused_without_changes(
    db: Session,
    playing: Callable[..., Any],
    fields: dict[str, Any],
    position: int | None,
    code: ErrorCode,
) -> None:
    game, row = playing(**fields)

    with pytest.raises(AppError) as error:
        confirm(game, db, position=position)

    assert error.value.code == code
    assert row.arrived_at is None


@pytest.mark.parametrize(("accuracy", "ok"), [(400, False), (8, True)])
def test_confirm_follows_whether_the_fix_is_weak(
    db: Session, playing: Callable[..., Any], accuracy: float, ok: bool
) -> None:
    game, row = playing(last_ping_at=None, last_distance_m=None)
    outcome = process_ping(game.student_id, game.id, Ping(LAT, LNG, accuracy), db)

    assert outcome.can_confirm is ok
    if not ok:
        with pytest.raises(AppError) as error:
            confirm(game, db)
        assert (error.value.code, error.value.details) == (
            ErrorCode.NOT_WITHIN_RADIUS,
            [{"reason": "gps_weak"}],
        )
        assert row.arrived_at is None
        return
    confirm(game, db)
    assert (row.status, row.flags) == (LocationStatus.VERIFYING, [])


def test_three_weak_pings_allow_manual_confirm(
    db: Session, playing: Callable[..., Any], freeze_clock: Callable[..., None]
) -> None:
    game, row = playing(started_at=NOW, last_ping_at=None, last_distance_m=None)
    for second in (0, 5, 10):
        freeze_clock(NOW + timedelta(seconds=second), *CLOCK)
        outcome = process_ping(game.student_id, game.id, Ping(LAT, LNG, 80), db)
    assert (outcome.can_confirm, row.weak_pings_in_row) == (False, 3)
    assert refused(game, db) == ErrorCode.NOT_WITHIN_RADIUS
    confirm(game, db, GPS)
    assert (row.arrival_override, row.flags) == (GPS, ["gps_override"])


def test_a_paused_game_already_verifying_repeats_without_writes(
    db: Session,
    playing: Callable[..., Any],
    freeze_clock: Callable[..., None],
    split_media_url: _Split,
) -> None:
    game, row = playing()
    confirm(game, db)
    game.state, game.paused_at = SessionState.PAUSED, NOW
    db.flush()
    freeze_clock(NOW + timedelta(minutes=1), *CLOCK)
    paused = confirm(game, db)

    _same_arrival(confirm(game, db), paused, split_media_url)
    assert paused.location.state == SessionState.PAUSED
    assert paused.location.stage == Stage.VERIFICATION
    assert (row.arrived_at, row.flags, game.last_activity_at) == (NOW, [], NOW)


def test_the_route(
    db: Session,
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    playing: Callable[..., Any],
    transaction_calls: list[str],
    assert_error: Callable[..., None],
    split_media_url: _Split,
) -> None:
    game, row = playing()
    student = db.get(User, game.student_id)
    assert student is not None
    url = f"/api/v1/games/{game.id}/navigation/confirm-arrival"
    headers = auth_headers(student)
    assert_error(
        client.post(url, json={"override_reason": "gps_unavailable"}, headers=headers),
        409,
        ErrorCode.GPS_OVERRIDE_NOT_ALLOWED,
    )
    bad = client.post(url, json={"override_reason": "lost"}, headers=headers)
    assert bad.status_code == 422
    transaction_calls.clear()

    response = client.post(url, json={"position": 1}, headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["stage"], body["position"], body["gps_override_available_at"]) == (
        "verification",
        1,
        None,
    )
    assert [item["code"] for item in body["feedback"]] == ["VERIFY_START"]
    assert transaction_calls.count("commit") == 1
    _same_route(client.post(url, json={}, headers=headers).json(), body, split_media_url)
    other = auth_headers(make_user())
    assert_error(client.post(url, json={}, headers=other), 404, ErrorCode.NOT_FOUND)
    admin = auth_headers(make_user(role="admin"))
    assert_error(client.post(url, json={}, headers=admin), 403, ErrorCode.FORBIDDEN_ROLE)
    assert_error(client.post(url, json={}), 401, ErrorCode.NOT_AUTHENTICATED)
