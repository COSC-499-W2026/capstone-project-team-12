"""utils/gps_replay.py (#46): reading routes and replaying them against a fake API."""

from typing import Any

import pytest
from gps_fakes import FakeSend, api_error, fake_game_api
from gps_replay import Api, RouteError, Step, read_route, replay

HEADER = "t_s,lat,lng,accuracy_m"
GAME = "game-1"
PING = f"/api/v1/games/{GAME}/navigation/ping"
STEPS = [Step(0, 49.94, -119.39, 5), Step(5, 49.9399, -119.39, 5), Step(30, 49.9395, -119.39, 60)]


def route(*rows: str) -> list[str]:
    return [HEADER, *rows]


def ping_answer(code: str, *, speak: bool = True, can_confirm: bool = False) -> dict[str, Any]:
    trend = "closer" if code == "NAV_GETTING_CLOSER" else None
    feedback = {"code": code, "audio_url": "/media/x"}
    return {"trend": trend, "can_confirm": can_confirm, "speak": speak, "feedback": feedback}


def playing(*pings: Any, stage: str = "navigation") -> FakeSend:
    """The fake API of GAME with these ping answers."""
    return fake_game_api(GAME, *pings, stage=stage)


def run(send: FakeSend, steps: list[Step] = STEPS, **kwargs: Any) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = replay(steps, Api(send, "token"), GAME, sleep=lambda _: None, out=lines.append, **kwargs)
    return code, lines


def test_a_route_is_read_in_order() -> None:
    steps = read_route(route("0,49.94,-119.39,5", "2.5,49.9399,-119.39,60.5"))

    assert steps == [Step(0, 49.94, -119.39, 5), Step(2.5, 49.9399, -119.39, 60.5)]


@pytest.mark.parametrize(
    ("lines", "problem"),
    [
        (["lat,lng,t_s,accuracy_m", "0,49.94,-119.39,5"], "header"),
        ([], "header"),
        (route(), "no rows"),
        (route("0,49.94,-119.39"), "line 2: every column must be a number"),
        (route("0,north,-119.39,5"), "line 2: every column must be a number"),
        (route("0,nan,-119.39,5"), "finite"),
        (route("0,49.94,-119.39,inf"), "finite"),
        (route("0,91,-119.39,5"), "out of range"),
        (route("0,49.94,-181,5"), "out of range"),
        (route("0,49.94,-119.39,-1"), "out of range"),
        (route("-1,49.94,-119.39,5"), "t_s"),
        (route("0,49.94,-119.39,5", "1.9,49.94,-119.39,5"), "line 3: t_s"),
    ],
)
def test_a_route_that_cant_be_replayed(lines: list[str], problem: str) -> None:
    with pytest.raises(RouteError, match=problem):
        read_route(lines)


def test_each_ping_is_printed_after_waiting_its_time() -> None:
    send = playing(
        (200, ping_answer("NAV_START")),
        (200, ping_answer("NAV_START", speak=False)),
        (200, ping_answer("NAV_GPS_WEAK")),
    )
    waits: list[float] = []

    code = replay(STEPS, Api(send, "token"), GAME, sleep=waits.append, out=lambda _: None)

    assert (code, waits) == (0, [0, 5, 25])
    assert [kwargs["json_body"] for _, _, kwargs in send.calls[1:]] == [
        {"lat": 49.94, "lng": -119.39, "accuracy_m": 5},
        {"lat": 49.9399, "lng": -119.39, "accuracy_m": 5},
        {"lat": 49.9395, "lng": -119.39, "accuracy_m": 60},
    ]
    assert {kwargs["token"] for _, _, kwargs in send.calls} == {"token"}


def test_the_output_shows_each_code_and_the_spoken_ones_at_the_end() -> None:
    send = playing(
        (200, ping_answer("NAV_START")),
        (200, ping_answer("NAV_START", speak=False)),
        (200, ping_answer("NAV_GETTING_CLOSER")),
    )

    code, lines = run(send)

    assert code == 0
    assert lines == [
        "game game-1, location 2: 3 pings",
        "t=    0s  acc    5 m  NAV_START            spoken  trend -        can_confirm=no",
        "t=    5s  acc    5 m  NAV_START            silent  trend -        can_confirm=no",
        "t=   30s  acc   60 m  NAV_GETTING_CLOSER   spoken  trend closer   can_confirm=no",
        "done: 3 pings, spoken NAV_START, NAV_GETTING_CLOSER",
    ]


def test_a_refused_ping_is_shown_and_the_replay_goes_on() -> None:
    send = playing(
        (200, ping_answer("NAV_START")),
        api_error(429, "RATE_LIMITED"),
        (200, ping_answer("NAV_GETTING_CLOSER")),
    )

    code, lines = run(send)

    assert code == 0
    assert lines[2] == "t=    5s  429 RATE_LIMITED: RATE_LIMITED message"
    assert len(lines) == 5


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (409, "SESSION_NOT_ACTIVE"),
        (409, "INVALID_STAGE"),
        (409, "STATE_CHANGED"),
        (401, "NOT_AUTHENTICATED"),  # e.g. the access token expired during a long route
        (403, "ACCOUNT_DISABLED"),
    ],
)
def test_the_replay_stops_when_the_game_or_the_login_changes(status: int, code: str) -> None:
    send = playing((200, ping_answer("NAV_START")), api_error(status, code))

    exit_code, lines = run(send)

    assert exit_code == 1
    assert lines[-1] == f"t=    5s  {status} {code}: {code} message"
    assert send.paths().count(PING) == 2


def test_confirm_on_the_first_ping_that_allows_it() -> None:
    send = playing(
        (200, ping_answer("NAV_START")), (200, ping_answer("NAV_ARRIVED", can_confirm=True))
    )

    code, lines = run(send, confirm=True)

    assert code == 0
    assert lines[-1] == "arrival confirmed: VERIFY_START, now in Stage 2 (verification)"
    method, path, kwargs = send.calls[-1]
    assert (method, path) == ("POST", f"/api/v1/games/{GAME}/navigation/confirm-arrival")
    assert kwargs["json_body"] == {"position": 2}
    assert len(send.calls) == 4  # the third step is never sent


def test_without_confirm_the_route_plays_to_the_end() -> None:
    allowed = ping_answer("NAV_ARRIVED", can_confirm=True)
    send = playing((200, allowed), (200, allowed), (200, allowed))

    code, _ = run(send)

    assert code == 0
    assert "/navigation/confirm-arrival" not in "".join(send.paths())
