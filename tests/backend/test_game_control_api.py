"""POST /api/v1/games/{id}/pause (A8) over HTTP.

The rules are tested in test_game_control.py. These check the status codes, the response body and
that the route commits, also when an idle game is expired before the 409.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy.orm import Session

from app.errors import ErrorCode
from app.game.sessions import session_idle_limit
from app.models.accounts import User
from app.models.enums import LocationStatus, SessionEndReason, SessionState, UserRole
from app.models.game import GameSession, SessionLocation

URL = "/api/v1/games"
ACTIONS = ["pause"]

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


@pytest.fixture
def post(client: TestClient, auth_headers: Callable[[User], dict[str, str]]) -> Callable[..., Any]:
    """POST a game action as the user."""

    def request(user: User, game_id: object, action: str) -> Response:
        return client.post(f"{URL}/{game_id}/{action}", headers=auth_headers(user))

    return request


def test_pause(
    db: Session,
    post: Callable[..., Response],
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    transaction_calls: list[str],
) -> None:
    student = make_user()
    game = make_game_session(student)
    make_session_location(game, 1, status=LocationStatus.VERIFYING, attempts_used=2)
    transaction_calls.clear()

    response = post(student, game.id, "pause")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == GAME_FIELDS | {"expires_at"}
    assert body["state"] == SessionState.PAUSED.value
    assert body["expires_at"].endswith("Z")
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert expires_at == datetime.fromisoformat(body["paused_at"]) + session_idle_limit()
    assert (body["locations"][0]["status"], body["locations"][0]["attempts_used"]) == (
        LocationStatus.VERIFYING.value,
        2,
    )
    # no room, answers or coordinates
    assert "SCI333" not in response.text
    assert transaction_calls.count("commit") == 1
    db.refresh(game)
    assert game.state == SessionState.PAUSED


def test_pausing_again_is_200(
    post: Callable[..., Response],
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
) -> None:
    student = make_user()
    game = make_game_session(student, state=SessionState.PAUSED, paused_at=datetime.now(UTC))

    response = post(student, game.id, "pause")

    assert response.status_code == 200, response.text
    assert response.json()["state"] == SessionState.PAUSED.value


@pytest.mark.parametrize("action", ACTIONS)
def test_idle_game_is_409_and_the_expiry_is_committed(
    db: Session,
    post: Callable[..., Response],
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    transaction_calls: list[str],
    action: str,
    assert_error: Callable[..., None],
) -> None:
    student = make_user()
    idle_since = datetime.now(UTC) - session_idle_limit() - timedelta(minutes=1)
    game = make_game_session(
        student, state=SessionState.PAUSED, paused_at=idle_since, last_activity_at=idle_since
    )
    transaction_calls.clear()

    assert_error(post(student, game.id, action), 409, ErrorCode.SESSION_NOT_ACTIVE)

    # committed before the 409, so the game doesn't look open on the next request
    assert transaction_calls.count("commit") == 1
    db.refresh(game)
    assert (game.state, game.end_reason) == (SessionState.ABANDONED, SessionEndReason.EXPIRED)


@pytest.mark.parametrize("action", ACTIONS)
def test_finished_game_is_409(
    post: Callable[..., Response],
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    action: str,
    assert_error: Callable[..., None],
) -> None:
    student = make_user()
    game = make_game_session(student, state=SessionState.COMPLETED, completed_at=datetime.now(UTC))

    response = post(student, game.id, action)

    assert_error(response, 409, ErrorCode.SESSION_NOT_ACTIVE)
    assert response.json()["error"]["details"] == [
        {"session_id": str(game.id), "state": SessionState.COMPLETED.value}
    ]


@pytest.mark.parametrize("action", ACTIONS)
def test_another_students_game_or_an_unknown_id_is_404(
    db: Session,
    post: Callable[..., Response],
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    action: str,
    assert_error: Callable[..., None],
) -> None:
    student = make_user()
    game = make_game_session(student)

    assert_error(post(make_user(), game.id, action), 404, ErrorCode.NOT_FOUND)
    assert_error(post(student, uuid4(), action), 404, ErrorCode.NOT_FOUND)
    db.refresh(game)
    assert game.state == SessionState.ACTIVE


@pytest.mark.parametrize("action", ACTIONS)
def test_an_id_that_is_not_a_uuid_is_422(
    post: Callable[..., Response],
    make_user: Callable[..., User],
    action: str,
    assert_error: Callable[..., None],
) -> None:
    assert_error(post(make_user(), "not-a-uuid", action), 422, ErrorCode.VALIDATION_ERROR)


@pytest.mark.parametrize("action", ACTIONS)
def test_admin_is_403(
    post: Callable[..., Response],
    make_user: Callable[..., User],
    action: str,
    assert_error: Callable[..., None],
) -> None:
    # the same as starting a game: admins don't play
    response = post(make_user(role=UserRole.ADMIN), uuid4(), action)

    assert_error(response, 403, ErrorCode.FORBIDDEN_ROLE)


@pytest.mark.parametrize("action", ACTIONS)
def test_disabled_account_is_403(
    post: Callable[..., Response],
    make_user: Callable[..., User],
    action: str,
    assert_error: Callable[..., None],
) -> None:
    assert_error(post(make_user(is_active=False), uuid4(), action), 403, ErrorCode.ACCOUNT_DISABLED)


@pytest.mark.parametrize("action", ACTIONS)
def test_needs_a_signed_in_user(
    client: TestClient, action: str, assert_error: Callable[..., None]
) -> None:
    response = client.post(f"{URL}/{uuid4()}/{action}")

    assert_error(response, 401, ErrorCode.NOT_AUTHENTICATED)
