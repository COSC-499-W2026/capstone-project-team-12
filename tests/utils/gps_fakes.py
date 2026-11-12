"""Fakes for the tests of utils/gps_replay.py, imported by name.

Not a conftest.py: tests/backend has one, and with both on the import path the backend tests'
`from conftest import ...` could pick up this folder's file instead."""

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
