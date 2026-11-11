"""GET /api/v1/me/games (A23, #47): the signed-in user's finished games, a page at a time."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy.orm import Session

from app.errors import ErrorCode
from app.game.sessions import session_idle_limit
from app.models.accounts import User
from app.models.engagement import Class
from app.models.enums import (
    Difficulty,
    Language,
    LocationStatus,
    SessionEndReason,
    SessionState,
    SkipReason,
    UserRole,
)
from app.models.game import GameSession, SessionLocation

URL = "/api/v1/me/games"
ITEM_FIELDS = {
    "session_id",
    "started_at",
    "ended_at",
    "language",
    "difficulty",
    "class_id",
    "state",
    "end_reason",
    "total_score",
    "locations_completed",
}
NOON = datetime(2026, 11, 2, 12, tzinfo=UTC)


@pytest.fixture
def get(client: TestClient, auth_headers: Callable[[User], dict[str, str]]) -> Callable[..., Any]:
    """GET the history as the user, with these query parameters."""

    def request(user: User, **params: Any) -> Response:
        return client.get(URL, params=params, headers=auth_headers(user))

    return request


@pytest.fixture
def finished(make_game_session: Callable[..., GameSession]) -> Callable[..., GameSession]:
    """A finished game of the student that ended at `ended`: completed unless a reason is given."""

    def make(
        student: User, ended: datetime, reason: SessionEndReason | None = None, **fields: Any
    ) -> GameSession:
        if reason is None:
            return make_game_session(
                student, state=SessionState.COMPLETED, completed_at=ended, **fields
            )
        return make_game_session(
            student,
            state=SessionState.ABANDONED,
            end_reason=reason,
            last_activity_at=ended,
            **fields,
        )

    return make


def ids(response: Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["session_id"] for item in response.json()["items"]]


def test_every_finished_game_is_listed_newest_first(
    get: Callable[..., Response],
    make_user: Callable[..., User],
    finished: Callable[..., GameSession],
    open_game: Callable[..., GameSession],
) -> None:
    student = make_user()
    games = [
        finished(student, NOON - timedelta(hours=hours), reason)
        for hours, reason in [
            (5, None),
            (4, SessionEndReason.QUIT),
            (3, SessionEndReason.RESET),
            (2, SessionEndReason.EXPIRED),
            (1, SessionEndReason.CLASS_CLOSED),
        ]
    ]
    open_game(student, SessionState.PAUSED)  # not history

    response = get(student)

    assert ids(response) == [str(game.id) for game in reversed(games)]
    body = response.json()
    paging = (body["page"], body["page_size"], body["total"], body["has_next_page"])
    assert paging == (1, 20, 5, False)
    reasons = [item["end_reason"] for item in body["items"]]
    assert reasons == ["class_closed", "expired", "reset", "quit", None]


def test_an_item_has_the_game_but_nothing_that_gives_a_location_away(
    get: Callable[..., Response],
    make_user: Callable[..., User],
    finished: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    game = finished(student, NOON, total_score=200)
    for position in (1, 2):
        make_session_location(
            game, position, status=LocationStatus.COMPLETED, points_awarded=100, completed_at=NOON
        )
    make_session_location(game, 3, status=LocationStatus.FAILED)
    # a shuffled-away row and its replacement at the same position
    make_session_location(game, 4, status=LocationStatus.SKIPPED, skip_reason=SkipReason.SHUFFLED)
    make_session_location(
        game, 4, status=LocationStatus.SKIPPED, skip_reason=SkipReason.DEACTIVATED
    )

    response = get(student)

    [item] = response.json()["items"]
    assert set(item) == ITEM_FIELDS
    assert item == item | {
        "session_id": str(game.id),
        "ended_at": "2026-11-02T12:00:00Z",
        "language": "ja",
        "difficulty": 1,
        "class_id": None,
        "state": "completed",
        "total_score": 200,
        "locations_completed": 2,
    }
    assert "SCI333" not in response.text


def test_a_class_game_has_its_class_and_difficulty(
    get: Callable[..., Response],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    finished: Callable[..., GameSession],
) -> None:
    student = make_user()
    school_class = make_class(target_language=Language.FR)
    finished(
        student,
        NOON,
        SessionEndReason.QUIT,
        class_id=school_class.id,
        target_language=Language.FR,
        difficulty=Difficulty.CHALLENGING,
    )

    [item] = get(student).json()["items"]

    expected = (str(school_class.id), "fr", Difficulty.CHALLENGING.value)
    assert (item["class_id"], item["language"], item["difficulty"]) == expected


def test_games_that_ended_at_the_same_time_come_newest_id_first(
    get: Callable[..., Response],
    make_user: Callable[..., User],
    finished: Callable[..., GameSession],
) -> None:
    student = make_user()
    games = [finished(student, NOON, SessionEndReason.EXPIRED) for _ in range(3)]

    assert ids(get(student)) == sorted((str(game.id) for game in games), reverse=True)


def test_language_filter(
    get: Callable[..., Response],
    make_user: Callable[..., User],
    finished: Callable[..., GameSession],
) -> None:
    student = make_user()
    japanese = finished(student, NOON)
    finished(student, NOON, target_language=Language.FR)

    response = get(student, language="ja")

    assert ids(response) == [str(japanese.id)]
    assert response.json()["total"] == 1


def test_pages(
    get: Callable[..., Response],
    make_user: Callable[..., User],
    finished: Callable[..., GameSession],
) -> None:
    student = make_user()
    games = [finished(student, NOON - timedelta(minutes=minute)) for minute in range(5)]
    newest_first = [str(game.id) for game in games]

    first, last, past_the_end = (get(student, page=page, page_size=2) for page in (1, 3, 4))

    assert ids(first) == newest_first[:2]
    assert (first.json()["total"], first.json()["has_next_page"]) == (5, True)
    assert ids(last) == newest_first[4:]
    assert last.json()["has_next_page"] is False
    assert ids(past_the_end) == []
    assert (past_the_end.json()["page"], past_the_end.json()["total"]) == (4, 5)


def test_a_new_student_has_an_empty_history(
    get: Callable[..., Response], make_user: Callable[..., User]
) -> None:
    response = get(make_user())

    assert response.json() == {
        "items": [],
        "page": 1,
        "page_size": 20,
        "total": 0,
        "has_next_page": False,
    }


def test_only_the_users_own_games(
    get: Callable[..., Response],
    make_user: Callable[..., User],
    finished: Callable[..., GameSession],
) -> None:
    student, other = make_user(), make_user()
    mine = finished(student, NOON)
    finished(other, NOON)

    assert ids(get(student)) == [str(mine.id)]


def test_staff_see_their_own_practice_games(
    get: Callable[..., Response],
    make_user: Callable[..., User],
    finished: Callable[..., GameSession],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    game = finished(teacher, NOON)

    assert ids(get(teacher)) == [str(game.id)]


def test_an_idle_open_game_is_expired_and_listed(
    db: Session,
    get: Callable[..., Response],
    make_user: Callable[..., User],
    open_game: Callable[..., GameSession],
    transaction_calls: list[str],
) -> None:
    student = make_user()
    idle_since = datetime.now(UTC) - session_idle_limit() - timedelta(minutes=1)
    game = open_game(student, last_activity_at=idle_since)
    transaction_calls.clear()

    response = get(student)

    assert ids(response) == [str(game.id)]
    assert response.json()["items"][0]["end_reason"] == "expired"
    assert transaction_calls.count("commit") == 1
    db.refresh(game)
    assert (game.state, game.end_reason) == (SessionState.ABANDONED, SessionEndReason.EXPIRED)


def test_refused(
    get: Callable[..., Response],
    client: TestClient,
    make_user: Callable[..., User],
    assert_error: Callable[..., None],
) -> None:
    student = make_user()

    assert_error(get(make_user(role=UserRole.ADMIN)), 403, ErrorCode.FORBIDDEN_ROLE)
    assert_error(client.get(URL), 401, ErrorCode.NOT_AUTHENTICATED)
    for params in (
        {"page": 0},
        {"page": 10_001},
        {"page": 10**18},  # would overflow the OFFSET
        {"page_size": 0},
        {"page_size": 101},
        {"language": "xx"},
    ):
        assert_error(get(student, **params), 422, ErrorCode.VALIDATION_ERROR)
