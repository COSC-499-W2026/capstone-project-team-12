"""Fakes for the tests of utils/gps_replay.py, imported by name.

Not a conftest.py: tests/backend has one, and with both on the import path the backend tests'
`from conftest import ...` could pick up this folder's file instead."""

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


class FakeSend:
    """A stand-in for gps_replay's Send: answers each call with the next queued answer for its
    method and path (an exception in the queue is raised), and records every call."""

    def __init__(self, answers: dict[str, list[Any]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        self.calls.append((method, path, kwargs))
        answer = self.answers[f"{method} {path}"].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def paths(self) -> list[str]:
        return [path for _, path, _ in self.calls]


def api_error(status: int, code: str) -> tuple[int, Any]:
    """An API error answer: the status and the standard error body, message "<CODE> message"."""
    return status, {"error": {"code": code, "message": f"{code} message", "details": []}}


def fake_game_api(game_id: str, *pings: Any, stage: str = "navigation") -> FakeSend:
    """A FakeSend for a game whose location 2 is current: these ping answers in order, and one
    confirm-arrival that starts Stage 2. Add answers for other calls to `.answers`."""
    games = f"/api/v1/games/{game_id}"
    arrival = {"stage": "verification", "feedback": [{"code": "VERIFY_START"}]}
    return FakeSend(
        {
            f"GET {games}/current-location": [(200, {"position": 2, "stage": stage})],
            f"POST {games}/navigation/ping": list(pings),
            f"POST {games}/navigation/confirm-arrival": [(200, arrival)],
        }
    )


class _EchoHandler(BaseHTTPRequestHandler):
    """Answers a POST with what it received as JSON: 409 for a path ending in /ping, 502 with an
    HTML page for /html, and a late 200 (after 1 s) for /slow."""

    def do_POST(self) -> None:  # noqa: N802 (http.server's name)
        received = {
            "path": self.path,
            "type": self.headers["Content-Type"],
            "auth": self.headers["Authorization"],
            "body": self.rfile.read(int(self.headers["Content-Length"])).decode(),
        }
        if self.path == "/html":
            self._reply(502, b"<html>Bad gateway</html>")
            return
        if self.path == "/slow":
            time.sleep(1)
        self._reply(409 if self.path.endswith("/ping") else 200, json.dumps(received).encode())

    def _reply(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@contextmanager
def echo_server() -> Iterator[str]:
    """The base URL of a local HTTP server that echoes each request (see _EchoHandler)."""
    server = HTTPServer(("127.0.0.1", 0), _EchoHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    server.server_close()
