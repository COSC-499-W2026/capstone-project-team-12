"""Replay a recorded walking route against a game (#46, spec: Simulation Scripts).

Logs in as the student, sends each position of the route to POST /games/{id}/navigation/ping
on the recorded timeline, and prints the feedback code the server returns at
each step. It only uses the public API, the same as the app.

The route is a CSV with the header t_s,lat,lng,accuracy_m: seconds since the start of the
walk, the position, and the GPS accuracy in metres. The times must increase by at least 2 s
(the server's ping rate limit). The server compares distances about 25 s apart, so the route
plays in real time. The positions must lead to the game's current location: the API never
says where that is.

Usage (from the repo root; the password is read from GPS_REPLAY_PASSWORD, or asked for):
    python utils/gps_replay.py route.csv --email student@example.com
    python utils/gps_replay.py route.csv --email ... --game <id> --confirm --base-url <url>
"""

from __future__ import annotations

import argparse
import csv
import getpass
import http.client
import json
import math
import os
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

COLUMNS = ("t_s", "lat", "lng", "accuracy_m")
MIN_GAP_S = 2.0  # the server refuses a ping less than 2 s after the last one (429)
API = "/api/v1"
# the replay can't go on after these: the game or its current location has changed, the login
# no longer works, or the server can't be reached. Others, such as 429, skip one step.
STOP_CODES = {"SESSION_NOT_ACTIVE", "INVALID_STAGE", "STATE_CHANGED", "NOT_FOUND"}
STOP_CODES |= {"NOT_AUTHENTICATED", "ACCOUNT_DISABLED", "FORBIDDEN_ROLE", "CONNECTION_FAILED"}
STOP_CODES |= {"UNEXPECTED_BODY"}  # a 200 that is not the JSON we need ends the replay


@dataclass(frozen=True)
class Step:
    t_s: float
    lat: float
    lng: float
    accuracy_m: float


class RouteError(ValueError):
    """A route file that can't be replayed."""


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code = status, code


def read_route(lines: Iterable[str]) -> list[Step]:
    """The steps of a route CSV, checked so that every row can be sent as a ping."""
    reader = csv.DictReader(lines)
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise RouteError(f"the header must be {','.join(COLUMNS)}")
    steps: list[Step] = []
    for row in reader:
        line = reader.line_num  # blank lines are not rows, so enumerate would number them wrong
        if row.get(None):
            raise RouteError(f"line {line}: unexpected extra columns")
        try:
            step = Step(*(float(row[name]) for name in COLUMNS))
        except (TypeError, ValueError):
            raise RouteError(f"line {line}: every column must be a number") from None
        if not all(math.isfinite(value) for value in vars(step).values()):
            raise RouteError(f"line {line}: every column must be a finite number")
        if not (-90 <= step.lat <= 90 and -180 <= step.lng <= 180) or step.accuracy_m < 0:
            raise RouteError(f"line {line}: lat, lng or accuracy_m out of range")
        if step.t_s < 0 or (steps and step.t_s - steps[-1].t_s < MIN_GAP_S):
            raise RouteError(f"line {line}: t_s must start at 0 or later and grow by >= 2 s")
        steps.append(step)
    if not steps:
        raise RouteError("the route has no rows")
    return steps


class Send(Protocol):
    """One API call: the HTTP status and the decoded JSON body."""

    def __call__(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json_body: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
    ) -> tuple[int, Any]: ...


def http_sender(base_url: str, timeout_s: float = 30) -> Send:
    """Send requests to a running backend with the standard library."""

    def send(
        method: str,
        path: str,
        *,
        token: str | None = None,
        json_body: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        headers = {"Accept": "application/json"}
        data = None
        if json_body is not None:
            data, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
        if form is not None:
            data = urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(base_url.rstrip("/") + path, data, headers, method=method)
        try:
            with urlopen(request, timeout=timeout_s) as response:
                return response.status, _decode(response.read())
        except HTTPError as error:
            try:
                raw = error.read()
            except (OSError, http.client.HTTPException):
                raw = b""
            error.close()
            return error.code, _decode(raw)
        except (OSError, http.client.HTTPException) as error:  # timeout, reset, or a cut body
            reason = error.reason if isinstance(error, URLError) else error
            raise ApiError(0, "CONNECTION_FAILED", str(reason) or type(error).__name__) from None

    return send


def _decode(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except ValueError:  # e.g. a proxy's HTML error page
        return raw.decode(errors="replace")


class Api:
    """The calls the replay makes, as one student. Errors raise ApiError."""

    def __init__(self, send: Send, token: str) -> None:
        self._send, self._token = send, token

    @classmethod
    def login(cls, send: Send, email: str, password: str) -> Api:
        form = {"username": email, "password": password}
        body = _fields(_check(*send("POST", f"{API}/auth/login", form=form)), "access_token")
        return cls(send, body["access_token"])

    def current_game(self) -> str:
        return str(_fields(self._call("GET", "/games/current"), "session_id")["session_id"])

    def current_location(self, game_id: str) -> dict[str, Any]:
        return _fields(self._call("GET", f"/games/{game_id}/current-location"))

    def ping(self, game_id: str, step: Step) -> dict[str, Any]:
        body = {"lat": step.lat, "lng": step.lng, "accuracy_m": step.accuracy_m}
        answer = self._call("POST", f"/games/{game_id}/navigation/ping", body)
        answer = _fields(answer, "speak", "feedback", "trend", "can_confirm")
        _fields(answer["feedback"], "code")
        return answer

    def confirm(self, game_id: str, position: int) -> dict[str, Any]:
        return _fields(
            self._call(
                "POST", f"/games/{game_id}/navigation/confirm-arrival", {"position": position}
            ),
            "stage",
            "feedback",
        )

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        return _check(*self._send(method, API + path, token=self._token, json_body=body))


def _fields(body: Any, *keys: str) -> dict[str, Any]:
    if isinstance(body, dict) and set(keys) <= body.keys():
        return body
    raise ApiError(200, "UNEXPECTED_BODY", repr(body)[:120])


def _check(status: int, body: Any) -> Any:
    if status >= 400:
        error = body.get("error", {}) if isinstance(body, dict) else {}
        raise ApiError(status, error.get("code", "HTTP_ERROR"), error.get("message", str(body)))
    return body


def describe(step: Step, answer: dict[str, Any]) -> str:
    """One line of output for a ping."""
    spoken = "spoken" if answer["speak"] else "silent"
    confirm = "yes" if answer["can_confirm"] else "no"
    return (
        f"t={step.t_s:>5.0f}s  acc {step.accuracy_m:>4.0f} m  "
        f"{answer['feedback']['code']:<20} {spoken}  trend {answer['trend'] or '-':<7}  "
        f"can_confirm={confirm}"
    )


def replay(
    steps: list[Step],
    api: Api,
    game_id: str,
    *,
    confirm: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Play the route against the game; 0 when it was played to the end (or arrival was
    confirmed), 1 when it had to stop."""
    try:
        location = api.current_location(game_id)
    except ApiError as error:
        out(f"can't replay: {error}")
        return 1
    if location.get("stage") != "navigation":
        out("can't replay: the game's current location is not in Stage 1 (navigation)")
        return 1
    if "position" not in location:
        out("can't replay: 200 UNEXPECTED_BODY: position")
        return 1
    out(f"game {game_id}, location {location['position']}: {len(steps)} pings")

    spoken: list[str] = []
    clock = time.monotonic if now is None else now
    start = clock()
    # catching up after a slow answer must not send the next ping inside the 2 s rate limit
    sent_at = start - MIN_GAP_S
    for step in steps:
        earliest = max(start + step.t_s, sent_at + MIN_GAP_S)
        sleep(max(0.0, earliest - clock()))
        sent_at = clock()  # when the ping is sent, not when its answer comes back
        try:
            answer = api.ping(game_id, step)
        except ApiError as error:
            out(f"t={step.t_s:>5.0f}s  {error}")
            if error.code in STOP_CODES:
                return 1
            continue
        out(describe(step, answer))
        if answer["speak"]:
            spoken.append(answer["feedback"]["code"])
        if confirm and answer["can_confirm"]:
            try:
                arrival = api.confirm(game_id, location["position"])
                codes = ", ".join(item["code"] for item in arrival["feedback"])
            except (ApiError, KeyError, TypeError) as error:
                if not isinstance(error, ApiError):
                    error = ApiError(200, "UNEXPECTED_BODY", "feedback")
                out(f"confirm-arrival: {error}")
                return 1
            out(f"arrival confirmed: {codes}, now in Stage 2 ({arrival['stage']})")
            return 0
    out(f"done: {len(steps)} pings, spoken {', '.join(spoken) or 'nothing'}")
    return 0


def main(argv: list[str] | None = None, send: Send | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay a recorded GPS route against a game.")
    parser.add_argument("route", type=Path, help="CSV with the header " + ",".join(COLUMNS))
    parser.add_argument("--email", required=True, help="the student's email")
    parser.add_argument("--game", help="the game id (default: the student's open game)")
    parser.add_argument("--confirm", action="store_true", help="confirm arrival when allowed")
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args(argv)

    try:
        with args.route.open(newline="", encoding="utf-8-sig") as file:
            steps = read_route(file)
    except (OSError, RouteError) as error:
        print(f"{args.route}: {error}", file=sys.stderr)
        return 2
    password = os.environ.get("GPS_REPLAY_PASSWORD") or getpass.getpass("Password: ")
    try:
        api = Api.login(send or http_sender(args.base_url), args.email, password)
        game_id = args.game or api.current_game()
    except ApiError as error:
        print(f"can't start the replay: {error}", file=sys.stderr)
        return 1
    return replay(steps, api, game_id, confirm=args.confirm, sleep=time.sleep)


if __name__ == "__main__":
    sys.exit(main())
