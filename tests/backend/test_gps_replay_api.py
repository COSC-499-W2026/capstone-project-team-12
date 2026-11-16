"""utils/gps_replay.py (#46) against the real routes, with the clock moved by the replay."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from gps_replay import Api, Step, read_route

from app.models.enums import LocationStatus

NOW = datetime(2026, 10, 29, 20, 0, tzinfo=UTC)
LAT, LNG = 49.9394, -119.3948  # the entrance make_session_location uses, radius 30 m
M = 1 / 111_195  # about one metre of latitude


def walk(*metres_north: float, every_s: float = 5, accuracy_m: float = 5) -> list[Step]:
    """A route CSV with one row every `every_s` seconds at these distances north of the target."""
    rows = [
        f"{i * every_s},{LAT + metres * M},{LNG},{accuracy_m}"
        for i, metres in enumerate(metres_north)
    ]
    return read_route(["t_s,lat,lng,accuracy_m", *rows])


def spoken(lines: list[str]) -> list[str]:
    """The codes spoken, from the ping lines ("t=  25s  acc    5 m  NAV_... spoken ...")."""
    return [line.split()[5] for line in lines if line.startswith("t=") and " spoken " in line]


def test_walking_in_at_1_4_m_s_and_confirming(replay_route: Callable[..., Any]) -> None:
    # 200 m out, 7 m every 5 s: inside the 30 m radius at 125 s
    route = walk(*(200 - 7 * i for i in range(30)))

    code, lines, row = replay_route(route, confirm=True, now=NOW)

    assert code == 0
    assert spoken(lines) == [
        "NAV_START",  # 0 s, 200 m
        "NAV_GETTING_CLOSER",  # 25 s, 165 m
        "NAV_GETTING_CLOSER",  # 50 s, 130 m
        "NAV_VERY_CLOSE",  # 75 s, 95 m
        "NAV_VERY_CLOSE",  # 100 s, 60 m
        "NAV_ARRIVED",  # 125 s, 25 m
    ]
    assert lines[-2].endswith("can_confirm=yes")
    assert lines[-1] == "arrival confirmed: VERIFY_START, now in Stage 2 (verification)"
    assert (row.status, row.arrived_at) == (LocationStatus.VERIFYING, NOW + timedelta(seconds=125))


def test_walking_away_then_back_plays_to_the_end(replay_route: Callable[..., Any]) -> None:
    away = [150 + 7 * i for i in range(6)]  # 150 m to 185 m
    route = walk(*away, *reversed(away))

    code, lines, row = replay_route(route, now=NOW)

    assert code == 0
    assert spoken(lines) == ["NAV_START", "NAV_GETTING_FARTHER", "NAV_GETTING_CLOSER"]
    assert lines[-1] == (
        "done: 12 pings, spoken NAV_START, NAV_GETTING_FARTHER, NAV_GETTING_CLOSER"
    )
    assert (row.status, row.last_ping_at) == (
        LocationStatus.NAVIGATING,
        NOW + timedelta(seconds=55),
    )


def test_a_weak_signal_is_spoken_every_25_s(replay_route: Callable[..., Any]) -> None:
    code, lines, row = replay_route(walk(*[100] * 7, accuracy_m=80), now=NOW)

    assert code == 0
    assert spoken(lines) == ["NAV_GPS_WEAK", "NAV_GPS_WEAK"]  # at 0 s and 25 s
    assert row.weak_pings_in_row == 3


def test_the_replay_stops_when_the_game_is_paused(
    replay_route: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    route = walk(200, 193)
    paused = {"done": False}
    original = Api.ping

    def ping_then_pause(api: Api, game_id: str, step: Step) -> Any:
        answer = original(api, game_id, step)
        if not paused["done"]:
            paused["done"] = True
            api._call("POST", f"/games/{game_id}/pause")
        return answer

    monkeypatch.setattr(Api, "ping", ping_then_pause)

    code, lines, row = replay_route(route, now=NOW)

    assert code == 1
    assert lines[-1].startswith("t=    5s  409 SESSION_NOT_ACTIVE")
    assert row.last_ping_at == NOW
