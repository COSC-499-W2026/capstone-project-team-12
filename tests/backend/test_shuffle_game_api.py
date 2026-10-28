"""POST /api/v1/games/{id}/shuffle (A9) over HTTP.

The rules are tested in test_shuffle_game.py. These check the request body, the status codes,
the response body and that the route commits, also when an idle game is expired before the 409.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy.orm import Session

from app.content import interface as content
from app.errors import ErrorCode
from app.game.sessions import session_idle_limit
from app.models.accounts import User
from app.models.enums import (
    Difficulty,
    Language,
    LocationStatus,
    SessionEndReason,
    SessionState,
    UserRole,
)
from app.models.game import GameSession, SessionLocation

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
    """POST a shuffle as the user, with `body` as the JSON body."""

    def request(user: User, game_id: object, body: object = None) -> Response:
        return client.post(
            f"/api/v1/games/{game_id}/shuffle",
            headers=auth_headers(user),
            json={"shuffles_used": 0} if body is None else body,
        )

    return request


@pytest.fixture
def playing(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> Callable[..., tuple[User, GameSession]]:
    """A student's game at position 2 (1 completed), with real locations so it can be shuffled."""

    def make(**fields: Any) -> tuple[User, GameSession]:
        eligible = sorted(content.eligible_location_ids(Language.JA, Difficulty.BASIC, db))
        student = make_user()
        game = make_game_session(student, current_position=2, total_score=100, **fields)
        make_session_location(
            game,
            1,
            location_id=eligible[0],
            status=LocationStatus.COMPLETED,
            points_awarded=100,
            completed_at=datetime.now(UTC),
        )
        make_session_location(game, 2, location_id=eligible[1], status=LocationStatus.NAVIGATING)
        return student, game

    return make


def test_shuffle(
    db: Session,
    post: Callable[..., Response],
    playing: Callable[..., tuple[User, GameSession]],
    transaction_calls: list[str],
) -> None:
    student, game = playing()
    transaction_calls.clear()

    response = post(student, game.id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == GAME_FIELDS | {"clue_audio_url"}
    assert (body["state"], body["shuffles_used"], body["total_score"]) == ("active", 1, 100)
    # the replaced row isn't listed: one row per position
    assert [(loc["position"], loc["status"]) for loc in body["locations"]] == [
        (1, LocationStatus.COMPLETED.value),
        (2, LocationStatus.NAVIGATING.value),
    ]
    assert body["clue_audio_url"].startswith("/api/v1/media/")
    assert "SCI333" not in response.text
    assert transaction_calls.count("commit") == 1
    db.refresh(game)
    assert game.shuffles_used == 1


def test_a_stale_shuffles_used_is_409_with_the_current_value(
    db: Session,
    post: Callable[..., Response],
    playing: Callable[..., tuple[User, GameSession]],
    assert_error: Callable[..., None],
) -> None:
    student, game = playing(shuffles_used=1)

    response = post(student, game.id, {"shuffles_used": 0})

    assert_error(response, 409, ErrorCode.STATE_CHANGED)
    assert response.json()["error"]["details"] == [
        {"session_id": str(game.id), "state": "active", "shuffles_used": 1, "current_position": 2}
    ]


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"shuffles_used": 2}, ErrorCode.SHUFFLE_LIMIT_REACHED),
        (
            {"state": SessionState.PAUSED, "paused_at": datetime.now(UTC)},
            ErrorCode.SESSION_NOT_ACTIVE,
        ),
        (
            {"state": SessionState.ABANDONED, "end_reason": SessionEndReason.QUIT},
            ErrorCode.SESSION_NOT_ACTIVE,
        ),
    ],
)
def test_refused_shuffles_are_409(
    db: Session,
    post: Callable[..., Response],
    playing: Callable[..., tuple[User, GameSession]],
    assert_error: Callable[..., None],
    fields: dict[str, Any],
    code: ErrorCode,
) -> None:
    student, game = playing(**fields)

    assert_error(post(student, game.id, {"shuffles_used": game.shuffles_used}), 409, code)
    db.refresh(game)
    assert game.shuffles_used == fields.get("shuffles_used", 0)


def test_idle_game_is_409_and_the_expiry_is_committed(
    db: Session,
    post: Callable[..., Response],
    playing: Callable[..., tuple[User, GameSession]],
    transaction_calls: list[str],
    assert_error: Callable[..., None],
) -> None:
    student, game = playing(
        last_activity_at=datetime.now(UTC) - session_idle_limit() - timedelta(minutes=1)
    )
    transaction_calls.clear()

    assert_error(post(student, game.id), 409, ErrorCode.SESSION_NOT_ACTIVE)

    assert transaction_calls.count("commit") == 1
    db.refresh(game)
    assert (game.state, game.end_reason) == (SessionState.ABANDONED, SessionEndReason.EXPIRED)


@pytest.mark.parametrize("body", [{}, {"shuffles_used": -1}, {"shuffles_used": "one"}])
def test_shuffles_used_is_required_and_not_negative(
    post: Callable[..., Response],
    playing: Callable[..., tuple[User, GameSession]],
    assert_error: Callable[..., None],
    body: dict[str, Any],
) -> None:
    student, game = playing()

    assert_error(post(student, game.id, body), 422, ErrorCode.VALIDATION_ERROR)


def test_another_students_game_or_an_unknown_id_is_404(
    post: Callable[..., Response],
    make_user: Callable[..., User],
    playing: Callable[..., tuple[User, GameSession]],
    assert_error: Callable[..., None],
) -> None:
    student, game = playing()

    assert_error(post(make_user(), game.id), 404, ErrorCode.NOT_FOUND)
    assert_error(post(student, uuid4()), 404, ErrorCode.NOT_FOUND)
    assert_error(post(student, "not-a-uuid"), 422, ErrorCode.VALIDATION_ERROR)


def test_admin_and_disabled_accounts_are_403(
    post: Callable[..., Response],
    make_user: Callable[..., User],
    assert_error: Callable[..., None],
) -> None:
    admin, disabled = make_user(role=UserRole.ADMIN), make_user(is_active=False)

    assert_error(post(admin, uuid4()), 403, ErrorCode.FORBIDDEN_ROLE)
    assert_error(post(disabled, uuid4()), 403, ErrorCode.ACCOUNT_DISABLED)


def test_needs_a_signed_in_user(client: TestClient, assert_error: Callable[..., None]) -> None:
    response = client.post(f"/api/v1/games/{uuid4()}/shuffle", json={"shuffles_used": 0})

    assert_error(response, 401, ErrorCode.NOT_AUTHENTICATED)
