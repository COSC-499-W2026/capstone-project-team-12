"""GET /api/v1/games/current (A25) over HTTP.

The rules are tested in test_game_status.py. These check the status codes, the response body and
that an expiry done while reading is committed.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import engine
from app.errors import ErrorCode
from app.game.sessions import session_idle_limit
from app.models.accounts import User
from app.models.enums import LocationStatus, SessionEndReason, SessionState
from app.models.game import GameSession, SessionLocation

URL = "/api/v1/games"

GAME_FIELDS = {
    "session_id",
    "state",
    "end_reason",
    "class_id",
    "language",
    "difficulty",
    "current_position",
    "shuffles_used",
    "total_score",
    "started_at",
    "paused_at",
    "locations",
}
LOCATION_FIELDS = {"position", "status", "skip_reason", "attempts_used", "points_awarded"}


@pytest.fixture
def get(client: TestClient, auth_headers: Callable[[User], dict[str, str]]) -> Callable[..., Any]:
    """GET a games URL as the user."""

    def request(user: User, path: str) -> Response:
        return client.get(f"{URL}/{path}", headers=auth_headers(user))

    return request


def assert_error(response: Response, status: int, code: ErrorCode) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code.value


def test_current_game_is_returned_without_giving_locations_away(
    get: Callable[..., Response],
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    game = make_game_session(student)
    make_session_location(game, 1, status=LocationStatus.NAVIGATING)

    response = get(student, "current")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == GAME_FIELDS
    assert (body["session_id"], body["state"]) == (str(game.id), SessionState.ACTIVE.value)
    assert set(body["locations"][0]) == LOCATION_FIELDS  # no room, answers or coordinates
    assert "SCI333" not in response.text


def test_times_are_utc_with_a_z(
    get: Callable[..., Response],
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
) -> None:
    student = make_user()
    make_game_session(student, state=SessionState.PAUSED, paused_at=datetime.now(UTC))

    body = get(student, "current").json()

    assert body["started_at"].endswith("Z")
    assert body["paused_at"].endswith("Z")


def test_the_app_connects_with_the_utc_time_zone() -> None:
    # the server's own time zone would otherwise decide how the times are written
    with engine.connect() as connection:
        assert connection.scalar(text("SHOW timezone")) == "UTC"


def test_no_open_game_is_404(get: Callable[..., Response], make_user: Callable[..., User]) -> None:
    assert_error(get(make_user(), "current"), 404, ErrorCode.NOT_FOUND)


def test_expiry_while_reading_is_committed(
    db: Session,
    get: Callable[..., Response],
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    transaction_calls: list[str],
) -> None:
    student = make_user()
    idle_since = datetime.now(UTC) - session_idle_limit() - timedelta(minutes=1)
    game = make_game_session(student, last_activity_at=idle_since)
    transaction_calls.clear()

    assert_error(get(student, "current"), 404, ErrorCode.NOT_FOUND)

    # committed before the 404, so the next read doesn't find the game open again
    assert transaction_calls.count("commit") == 1
    db.refresh(game)
    assert (game.state, game.end_reason) == (SessionState.ABANDONED, SessionEndReason.EXPIRED)


def test_needs_a_signed_in_user(client: TestClient) -> None:
    response = client.get(f"{URL}/current")

    assert_error(response, 401, ErrorCode.NOT_AUTHENTICATED)


def test_disabled_account_is_403(
    get: Callable[..., Response], make_user: Callable[..., User]
) -> None:
    assert_error(get(make_user(is_active=False), "current"), 403, ErrorCode.ACCOUNT_DISABLED)
