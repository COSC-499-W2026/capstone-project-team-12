"""utils/gps_replay.py (#46) against the real routes, with the clock moved by the replay."""

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from gps_replay import Step, read_route

from app.models.enums import LocationStatus

_CODE = re.compile(r"\b(?:NAV|VERIFY)_[A-Z_]+")

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
    """The codes spoken on ping lines, matched so a change in spacing does not move the index."""
    found: list[str] = []
    for line in lines:
        if line.startswith("t=") and " spoken " in line:
            match = _CODE.search(line)
            assert match is not None
            found.append(match.group(0))
    return found


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
        "done: 12 pings, 0 refused, spoken NAV_START, NAV_GETTING_FARTHER, NAV_GETTING_CLOSER"
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


def test_the_replay_stops_when_the_game_is_paused(replay_route: Callable[..., Any]) -> None:
    route = walk(200, 193)
    done = {"paused": False}

    def pause_after_ping(http: TestClient, token: str, game_id: str) -> None:
        if done["paused"]:
            return
        done["paused"] = True
        response = http.post(
            f"/api/v1/games/{game_id}/pause", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200

    code, lines, row = replay_route(route, now=NOW, after_ping=pause_after_ping)

    assert code == 1
    assert lines[-1].startswith("t=    5s  409 SESSION_NOT_ACTIVE")
    assert row.last_ping_at == NOW
