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
from jwt import encode
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content import interface as content
from app.errors import ErrorCode
from app.models.accounts import User
from app.models.engagement import Class, ClassEnrollment
from app.models.enums import (
    Difficulty,
    EnrollmentEndReason,
    Language,
    SessionState,
    UserRole,
)
from app.models.game import GameSession

URL = "/api/v1/games"
KEY = str(get_settings().access_token_secret_key)


def login(user: User) -> dict[str, str]:
    """The Authorization header for a user."""
    exp = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
    return {"Authorization": f"Bearer {encode({'sub': str(user.id), 'exp': exp}, KEY, 'HS256')}"}


def start(client: TestClient, user: User, **body: Any) -> Response:
    return client.post(URL, json=body, headers=login(user))


def error_code(response: Response) -> str:
    return str(response.json()["error"]["code"])


def test_class_game_is_created(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    student, school_class = make_user(), make_class(target_language=Language.JA)
    make_enrollment(student, school_class)

    response = start(client, student, difficulty=Difficulty.BASIC, class_id=str(school_class.id))

    # 201, and nothing but the session, its state, the position and the first clue
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"session_id", "state", "position", "clue_audio_url"}
    assert (body["state"], body["position"]) == (SessionState.ACTIVE, 1)
    assert body["clue_audio_url"].startswith("/api/v1/media/")
    game = db.get_one(GameSession, body["session_id"])
    assert (game.student_id, game.class_id) == (student.id, school_class.id)


def test_practice_game_is_created(
    client: TestClient, db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user(role=UserRole.STAFF)

    response = start(client, student, difficulty=2, language="ko")

    assert response.status_code == 201
    game = db.get_one(GameSession, response.json()["session_id"])
    assert (game.class_id, game.target_language) == (None, Language.KO)
    assert game.difficulty == Difficulty.ADVANCING


def test_the_route_commits(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    student = make_user()
    calls: list[str] = []
    real_commit = db.commit
    monkeypatch.setattr(db, "commit", lambda: (calls.append("commit"), real_commit()))

    start(client, student, difficulty=1, language="ja")

    # start_game never commits, so this one is the route's
    assert calls == ["commit"]


def test_open_game_returns_409_with_its_id(
    client: TestClient, db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user()
    first = start(client, student, difficulty=1, language="ja")

    second = start(client, student, difficulty=1, language="ja")

    assert second.status_code == 409
    assert error_code(second) == ErrorCode.SESSION_ALREADY_OPEN
    assert second.json()["error"]["details"] == [{"session_id": first.json()["session_id"]}]
    assert db.scalar(select(func.count()).where(GameSession.student_id == student.id)) == 1


def test_not_enough_content_returns_409(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    four = frozenset(list(content.eligible_location_ids(Language.JA, Difficulty.BASIC, db))[:4])
    monkeypatch.setattr(content, "eligible_location_ids", lambda *_: four)

    response = start(client, make_user(), difficulty=1, language="ja")

    assert response.status_code == 409
    assert error_code(response) == ErrorCode.NOT_ENOUGH_CONTENT


def test_class_the_student_is_not_in_returns_404(
    client: TestClient, make_user: Callable[..., User], make_class: Callable[..., Class]
) -> None:
    response = start(client, make_user(), difficulty=1, class_id=str(make_class().id))

    assert response.status_code == 404
    assert error_code(response) == ErrorCode.NOT_FOUND


def test_archived_class_returns_409(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    student, school_class = make_user(), make_class(archived_at=datetime.now(UTC))
    make_enrollment(
        student,
        school_class,
        is_active=False,
        end_reason=EnrollmentEndReason.ARCHIVED,
        left_at=datetime.now(UTC),
    )

    response = start(client, student, difficulty=1, class_id=str(school_class.id))

    assert response.status_code == 409
    assert error_code(response) == ErrorCode.CLASS_ARCHIVED


def test_admins_get_403(client: TestClient, make_user: Callable[..., User]) -> None:
    response = start(client, make_user(role=UserRole.ADMIN), difficulty=1, language="ja")

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
    client: TestClient, make_user: Callable[..., User], body: dict[str, Any]
) -> None:
    response = start(client, make_user(), **body)

    assert response.status_code == 422
    assert error_code(response) == ErrorCode.VALIDATION_ERROR
