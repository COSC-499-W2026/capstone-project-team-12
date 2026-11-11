"""utils/gps_replay.py (#46): reading routes and replaying them against a fake API."""

from collections.abc import Iterator
from typing import Any

import gps_replay
import pytest
from gps_fakes import FakeSend, api_error, echo_server, fake_game_api
from gps_replay import Api, ApiError, RouteError, Step, read_route, replay

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
        (route("0,49.94,-119.39,5,1"), "line 2: unexpected extra columns"),
        ([HEADER, "", "0,49.94,-119.39,5", "1,49.94,-119.39,5"], "line 4: t_s"),
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
    clock = {"t": 0.0}

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock["t"] += seconds

    code = replay(
        STEPS, Api(send, "token"), GAME, sleep=sleep, now=lambda: clock["t"], out=lambda _: None
    )

    assert (code, waits) == (0, [0, 5, 25])
    assert [kwargs["json_body"] for _, _, kwargs in send.calls[1:]] == [
        {"lat": 49.94, "lng": -119.39, "accuracy_m": 5},
        {"lat": 49.9399, "lng": -119.39, "accuracy_m": 5},
        {"lat": 49.9395, "lng": -119.39, "accuracy_m": 60},
    ]
    assert {kwargs["token"] for _, _, kwargs in send.calls} == {"token"}


def test_a_slow_answer_comes_out_of_the_next_wait() -> None:
    send = playing((200, ping_answer("NAV_START")), (200, ping_answer("NAV_START", speak=False)))
    clock = {"t": 0.0}

    def slow(method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        if path.endswith("/ping"):
            clock["t"] += 1  # this answer took a second of the recorded gap
        return FakeSend.__call__(send, method, path, **kwargs)

    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock["t"] += seconds

    steps = [Step(0, 49.94, -119.39, 5), Step(5, 49.9399, -119.39, 5)]
    code = replay(
        steps, Api(slow, "token"), GAME, sleep=sleep, now=lambda: clock["t"], out=lambda _: None
    )

    assert (code, waits) == (0, [0, 4])


def test_catching_up_after_a_slow_answer_keeps_the_rate_limit() -> None:
    send = playing((200, ping_answer("NAV_START")), (200, ping_answer("NAV_START", speak=False)))
    clock = {"t": 0.0}

    def slow(method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        if path.endswith("/ping"):
            clock["t"] += 4  # four of the five seconds were spent waiting for this answer
        return FakeSend.__call__(send, method, path, **kwargs)

    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock["t"] += seconds

    steps = [Step(0, 49.94, -119.39, 5), Step(5, 49.9399, -119.39, 5)]
    code = replay(
        steps, Api(slow, "token"), GAME, sleep=sleep, now=lambda: clock["t"], out=lambda _: None
    )

    assert (code, waits) == (0, [0, 1])  # the next ping still goes out at the recorded 5 s


def test_a_one_second_answer_keeps_a_two_second_route_on_time() -> None:
    """Send times stay on 0, 2, 4, 6 when each answer takes a second."""
    send = playing(*[(200, ping_answer("NAV_START", speak=False)) for _ in range(4)])
    clock = {"t": 0.0}
    sent: list[float] = []

    def slow(method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        if path.endswith("/ping"):
            sent.append(clock["t"])
            clock["t"] += 1
        return FakeSend.__call__(send, method, path, **kwargs)

    def sleep(seconds: float) -> None:
        clock["t"] += seconds

    steps = [Step(t, 49.94, -119.39, 5) for t in (0, 2, 4, 6)]
    code = replay(
        steps, Api(slow, "token"), GAME, sleep=sleep, now=lambda: clock["t"], out=lambda _: None
    )

    assert code == 0
    assert sent == [0, 2, 4, 6]


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


def test_the_replay_stops_when_the_server_cant_be_reached() -> None:
    lost = ApiError(0, "CONNECTION_FAILED", "timed out")
    send = playing((200, ping_answer("NAV_START")), lost)

    exit_code, lines = run(send)

    assert (exit_code, lines[-1]) == (1, "t=    5s  0 CONNECTION_FAILED: timed out")
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


def test_a_refused_confirm_stops_the_replay() -> None:
    send = playing((200, ping_answer("NAV_ARRIVED", can_confirm=True)))
    confirm = f"POST /api/v1/games/{GAME}/navigation/confirm-arrival"
    send.answers[confirm] = [api_error(409, "STALE_POSITION")]

    code, lines = run(send, confirm=True)

    assert (code, lines[-1]) == (1, "confirm-arrival: 409 STALE_POSITION: STALE_POSITION message")


@pytest.mark.parametrize(
    "location",
    [
        {"position": 2, "stage": "verification"},
        {"session_id": GAME, "state": "completed", "locations": []},  # the game is over
    ],
)
def test_only_a_location_in_stage_1(location: dict[str, Any]) -> None:
    send = playing()
    send.answers[f"GET /api/v1/games/{GAME}/current-location"] = [(200, location)]

    code, lines = run(send)

    assert (code, len(send.calls)) == (1, 1)
    assert "not in Stage 1" in lines[0]


def test_a_game_that_cant_be_read() -> None:
    send = playing()
    send.answers[f"GET /api/v1/games/{GAME}/current-location"] = [api_error(404, "NOT_FOUND")]

    code, lines = run(send)

    assert (code, lines) == (1, ["can't replay: 404 NOT_FOUND: NOT_FOUND message"])


@pytest.fixture
def server() -> Iterator[str]:
    with echo_server() as url:
        yield url


@pytest.fixture
def route_file(tmp_path: Any) -> Any:
    path = tmp_path / "route.csv"
    path.write_text("\n".join(route("0,49.94,-119.39,5", "2,49.9399,-119.39,5")), "utf-8")
    return path


def test_main_logs_in_and_replays_the_open_game_in_real_time(
    route_file: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GPS_REPLAY_PASSWORD", "secret")
    waits: list[float] = []
    clock = {"t": 0.0}

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr(gps_replay.time, "sleep", sleep)
    monkeypatch.setattr(gps_replay.time, "monotonic", lambda: clock["t"])
    send = playing((200, ping_answer("NAV_START")), (200, ping_answer("NAV_START", speak=False)))
    send.answers["POST /api/v1/auth/login"] = [(200, {"access_token": "abc"})]
    send.answers["GET /api/v1/games/current"] = [(200, {"session_id": GAME})]

    code = gps_replay.main([str(route_file), "--email", "s@example.com"], send=send)

    assert (code, waits) == (0, [0, 2])  # the script waits with time.sleep
    assert send.calls[0] == (
        "POST",
        "/api/v1/auth/login",
        {"form": {"username": "s@example.com", "password": "secret"}},
    )
    assert send.calls[1][2]["token"] == "abc"
    assert "NAV_START" in capsys.readouterr().out


def test_main_with_a_game_id_and_a_failed_login(
    route_file: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GPS_REPLAY_PASSWORD", "wrong")
    send = playing()
    send.answers["POST /api/v1/auth/login"] = [api_error(401, "INVALID_CREDENTIALS")]

    code = gps_replay.main([str(route_file), "--email", "s@x.com", "--game", GAME], send=send)

    assert (code, len(send.calls)) == (1, 1)
    assert "401 INVALID_CREDENTIALS" in capsys.readouterr().err


def test_main_refuses_a_bad_route(tmp_path: Any, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("lat,lng\n", encoding="utf-8")
    send = playing()

    assert gps_replay.main([str(bad), "--email", "s@x.com"], send=send) == 2
    assert gps_replay.main([str(tmp_path / "missing.csv"), "--email", "s@x.com"], send=send) == 2
    assert send.calls == []
    assert "the header must be" in capsys.readouterr().err


def test_http_sender_sends_json_forms_and_the_token(server: str) -> None:
    send = gps_replay.http_sender(server)

    status, body = send("POST", "/api/v1/auth/login", form={"username": "a@b.c", "password": "p"})
    assert status == 200
    assert body["type"] == "application/x-www-form-urlencoded"
    assert body["body"] == "username=a%40b.c&password=p"

    status, body = send("POST", "/x/ping", token="abc", json_body={"lat": 1})
    assert status == 409
    assert (body["path"], body["auth"]) == ("/x/ping", "Bearer abc")
    assert (body["type"], body["body"]) == ("application/json", '{"lat": 1}')

    assert send("POST", "/html", json_body={}) == (502, "<html>Bad gateway</html>")


@pytest.mark.parametrize(
    ("url", "path"),
    [
        ("http://127.0.0.1:9", "/api/v1/games/current"),  # nothing listens on port 9
        (None, "/slow"),  # the server accepts the request but answers too late
    ],
)
def test_a_server_that_doesnt_answer(server: str, url: str | None, path: str) -> None:
    send = gps_replay.http_sender(url or server, timeout_s=0.2)

    with pytest.raises(ApiError) as failure:
        send("POST", path, json_body={})
    assert (failure.value.status, failure.value.code) == (0, "CONNECTION_FAILED")


def test_main_reads_a_route_saved_with_an_excel_bom(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "route.csv"
    path.write_bytes(b"\xef\xbb\xbf" + b"t_s,lat,lng,accuracy_m\n0,49.94,-119.39,5\n")
    monkeypatch.setenv("GPS_REPLAY_PASSWORD", "secret")
    monkeypatch.setattr(gps_replay.time, "sleep", lambda _seconds: None)
    send = playing((200, ping_answer("NAV_START")))
    send.answers["POST /api/v1/auth/login"] = [(200, {"access_token": "abc"})]

    code = gps_replay.main([str(path), "--email", "s@x.com", "--game", GAME], send=send)

    assert code == 0
    assert send.paths()[-1].endswith("/navigation/ping")
