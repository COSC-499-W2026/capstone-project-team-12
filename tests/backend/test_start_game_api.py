"""POST /api/v1/games (A7, A11): the route around start_game, over HTTP.

start_game's own rules are in test_start_game.py. These tests check what the route adds: the
request body, the access token, the status codes, the response body and the commit.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.content import interface as content
from app.errors import ErrorCode
from app.models.accounts import User
from app.models.engagement import Class, ClassEnrollment
from app.models.enums import (
    Difficulty,
    EnrollmentEndReason,
    Language,
    SessionEndReason,
    SessionState,
    UserRole,
)
from app.models.game import GameSession
from app.services.storage import get_storage_service

URL = "/api/v1/games"

StartGame = Callable[..., Response]


@pytest.fixture
def start(client: TestClient, auth_headers: Callable[[User], dict[str, str]]) -> StartGame:
    """POST /games as `user` with the given JSON body."""

    def post(user: User, **body: Any) -> Response:
        return client.post(URL, json=body, headers=auth_headers(user))

    return post


def error_code(response: Response) -> str:
    return str(response.json()["error"]["code"])


def test_class_game_is_created(
    start: StartGame,
    db: Session,
    split_media_url: Callable[[str], tuple[str, int, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    student, school_class = make_user(), make_class(target_language=Language.JA)
    make_enrollment(student, school_class)

    response = start(student, difficulty=Difficulty.BASIC, class_id=str(school_class.id))

    # 201, and nothing but the session, its state, the position and the first clue
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"session_id", "state", "position", "clue_audio_url"}
    assert (body["state"], body["position"]) == (SessionState.ACTIVE, 1)
    key, expires, signature = split_media_url(body["clue_audio_url"])
    get_storage_service().verify(key, expires, signature)
    game = db.get_one(GameSession, body["session_id"])
    assert (game.student_id, game.class_id) == (student.id, school_class.id)


def test_practice_game_is_created(
    start: StartGame, db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user(role=UserRole.STAFF)

    response = start(student, difficulty=2, language="ko")

    assert response.status_code == 201
    game = db.get_one(GameSession, response.json()["session_id"])
    assert (game.class_id, game.target_language) == (None, Language.KO)
    assert game.difficulty == Difficulty.ADVANCING


def test_the_route_commits(
    start: StartGame, make_user: Callable[..., User], transaction_calls: list[str]
) -> None:
    student = make_user()
    transaction_calls.clear()

    start(student, difficulty=1, language="ja")

    # start_game itself never commits, but its savepoint (for the one-open-game race) fires one
    # commit event too. The other is the route's own commit.
    assert transaction_calls.count("commit") == 2


def test_open_game_returns_409_with_its_id(
    start: StartGame, db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user()
    first = start(student, difficulty=1, language="ja")

    second = start(student, difficulty=1, language="ja")

    assert second.status_code == 409
    assert error_code(second) == ErrorCode.SESSION_ALREADY_OPEN
    assert second.json()["error"]["details"] == [{"session_id": first.json()["session_id"]}]
    assert db.scalar(select(func.count()).where(GameSession.student_id == student.id)) == 1


def test_idle_open_game_is_expired_and_the_new_game_starts(
    start: StartGame,
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
) -> None:
    student = make_user()
    idle = make_game_session(student, last_activity_at=datetime.now(UTC) - timedelta(hours=25))

    response = start(student, difficulty=1, language="ja")

    assert response.status_code == 201
    db.refresh(idle)
    assert (idle.state, idle.end_reason) == (SessionState.ABANDONED, SessionEndReason.EXPIRED)
    assert response.json()["session_id"] != str(idle.id)


def test_not_enough_content_returns_409(
    start: StartGame,
    db: Session,
    make_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    four = frozenset(list(content.eligible_location_ids(Language.JA, Difficulty.BASIC, db))[:4])
    monkeypatch.setattr(content, "eligible_location_ids", lambda *_: four)

    response = start(make_user(), difficulty=1, language="ja")

    assert response.status_code == 409
    assert error_code(response) == ErrorCode.NOT_ENOUGH_CONTENT


def test_class_the_student_is_not_in_returns_404(
    start: StartGame, make_user: Callable[..., User], make_class: Callable[..., Class]
) -> None:
    response = start(make_user(), difficulty=1, class_id=str(make_class().id))

    assert response.status_code == 404
    assert error_code(response) == ErrorCode.NOT_FOUND


def test_archived_class_returns_409(
    start: StartGame,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    student, school_class = make_user(), make_class(archived_at=datetime.now(UTC))
    enrollment = make_enrollment(student, school_class)
    enrollment.is_active = False
    enrollment.end_reason = EnrollmentEndReason.ARCHIVED
    enrollment.left_at = max(datetime.now(UTC), enrollment.joined_at)
    db.flush()

    response = start(student, difficulty=1, class_id=str(school_class.id))

    assert response.status_code == 409
    assert error_code(response) == ErrorCode.CLASS_ARCHIVED


def test_admins_get_403(start: StartGame, make_user: Callable[..., User]) -> None:
    response = start(make_user(role=UserRole.ADMIN), difficulty=1, language="ja")

    assert response.status_code == 403
    assert error_code(response) == ErrorCode.FORBIDDEN_ROLE


def test_no_token_gets_401(client: TestClient) -> None:
    response = client.post(URL, json={"difficulty": 1, "language": "ja"})

    assert response.status_code == 401
    assert error_code(response) == ErrorCode.NOT_AUTHENTICATED


@pytest.mark.parametrize(
    "body",
    [
        {"difficulty": 1},
        {"difficulty": 1, "language": "ja", "class_id": str(uuid4())},
        {"language": "ja"},
        {"difficulty": 4, "language": "ja"},
        {"difficulty": 1, "language": "xx"},
        {"difficulty": 1, "class_id": "not-a-uuid"},
    ],
)
def test_bad_body_gets_422_not_500(
    start: StartGame, make_user: Callable[..., User], body: dict[str, Any]
) -> None:
    response = start(make_user(), **body)

    assert response.status_code == 422
    assert error_code(response) == ErrorCode.VALIDATION_ERROR
