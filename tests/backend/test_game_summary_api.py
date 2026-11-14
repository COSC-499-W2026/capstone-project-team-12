"""GET /api/v1/games/{id}/summary (A23, #47): a game's results and what it added to the
student's level and badges. get_game_engagement's rules are tested in test_game_engagement.py."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy.orm import Session

from app.engagement.interface import apply_location_result
from app.errors import ErrorCode
from app.game.sessions import session_idle_limit
from app.models.accounts import User
from app.models.engagement import PointsLedger
from app.models.enums import (
    Language,
    LocationStatus,
    PointsSourceType,
    SessionEndReason,
    SessionState,
    SkipReason,
    UserRole,
)
from app.models.game import GameSession, SessionLocation

URL = "/api/v1/games"
SUMMARY_FIELDS = {
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
    "ended_at",
    "engagement",
}
NOON = datetime(2026, 11, 2, 12, tzinfo=UTC)


@pytest.fixture
def summary(
    client: TestClient, auth_headers: Callable[[User], dict[str, str]]
) -> Callable[..., Any]:
    """GET a game's summary as the user."""

    def request(user: User, game_id: UUID | str) -> Response:
        return client.get(f"{URL}/{game_id}/summary", headers=auth_headers(user))

    return request


@pytest.fixture
def completed_location(
    make_session_location: Callable[..., SessionLocation],
) -> Callable[..., SessionLocation]:
    def make(game: GameSession, position: int) -> SessionLocation:
        return make_session_location(
            game,
            position,
            status=LocationStatus.COMPLETED,
            points_awarded=100,
            completed_at=datetime.now(UTC),
        )

    return make


def award(db: Session, student: User, location_id: UUID, points: int, at: datetime) -> None:
    """A ledger row for a location, as scoring writes it, at a set time."""
    db.add(
        PointsLedger(
            user_id=student.id,
            source_type=PointsSourceType.LOCATION,
            source_ref=str(location_id),
            language=Language.JA,
            points=points,
            created_at=at,
        )
    )
    db.flush()


def test_a_completed_game_with_its_points_and_first_badge(
    db: Session,
    summary: Callable[..., Response],
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    completed_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    game = make_game_session(
        student, state=SessionState.COMPLETED, completed_at=NOON, total_score=100
    )
    location = completed_location(game, 1)
    # the scoring the finishing step does: a ledger row and the first-location badge
    apply_location_result(
        location.id,
        student.id,
        completed=True,
        target_language=game.target_language,
        class_id=None,
        db=db,
    )

    response = summary(student, game.id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == SUMMARY_FIELDS
    assert body == body | {
        "session_id": str(game.id),
        "state": "completed",
        "ended_at": "2026-11-02T12:00:00Z",
        "total_score": 100,
    }
    engagement = body["engagement"]
    assert (engagement["level"], engagement["level_up"]) == (1, False)
    assert [badge["achievement_key"] for badge in engagement["new_badges"]] == ["first_step"]
    assert set(engagement["new_badges"][0]) == {
        "achievement_key",
        "icon_key",
        "language",
        "unlocked_at",
    }
    assert "SCI333" not in response.text  # no answers, rooms or coordinates


def test_the_level_is_the_one_the_game_reached(
    db: Session,
    summary: Callable[..., Response],
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    completed_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    award(db, student, uuid4(), 400, NOON - timedelta(days=1))  # an earlier game
    game = make_game_session(student, state=SessionState.COMPLETED, completed_at=NOON)
    award(db, student, completed_location(game, 1).id, 100, NOON)  # 500 points: level 2
    award(db, student, uuid4(), 1000, NOON + timedelta(days=1))  # a later game

    engagement = summary(student, game.id).json()["engagement"]

    assert (engagement["level"], engagement["level_up"]) == (2, True)


def test_a_quit_game_ended_when_it_was_quit_and_shuffled_rows_are_left_out(
    summary: Callable[..., Response],
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    game = make_game_session(
        student,
        state=SessionState.ABANDONED,
        end_reason=SessionEndReason.QUIT,
        last_activity_at=NOON,
    )
    make_session_location(game, 1, status=LocationStatus.SKIPPED, skip_reason=SkipReason.SHUFFLED)
    make_session_location(
        game, 1, status=LocationStatus.SKIPPED, skip_reason=SkipReason.SESSION_ENDED
    )

    body = summary(student, game.id).json()

    assert (body["state"], body["end_reason"], body["ended_at"]) == (
        "abandoned",
        "quit",
        "2026-11-02T12:00:00Z",
    )
    assert [location["skip_reason"] for location in body["locations"]] == ["session_ended"]
    assert body["engagement"] == {"level": 1, "level_up": False, "new_badges": []}


def test_an_open_game_shows_its_results_so_far(
    summary: Callable[..., Response],
    make_user: Callable[..., User],
    open_game: Callable[..., GameSession],
    completed_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    game = open_game(student, SessionState.PAUSED)
    completed_location(game, 1)

    body = summary(student, game.id).json()

    assert (body["state"], body["ended_at"]) == ("paused", None)
    assert body["locations"][0]["status"] == "completed"


def test_staff_read_the_summary_of_their_practice_game(
    summary: Callable[..., Response],
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    game = make_game_session(teacher, state=SessionState.COMPLETED, completed_at=NOON)

    response = summary(teacher, game.id)

    assert response.status_code == 200, response.text
    assert (response.json()["session_id"], response.json()["class_id"]) == (str(game.id), None)


def test_an_idle_open_game_is_expired_when_read(
    db: Session,
    summary: Callable[..., Response],
    make_user: Callable[..., User],
    open_game: Callable[..., GameSession],
    transaction_calls: list[str],
) -> None:
    student = make_user()
    idle_since = datetime.now(UTC) - session_idle_limit() - timedelta(minutes=1)
    game = open_game(student, last_activity_at=idle_since)
    transaction_calls.clear()

    body = summary(student, game.id).json()

    assert (body["state"], body["end_reason"]) == ("abandoned", "expired")
    assert body["ended_at"] is not None
    assert transaction_calls.count("commit") == 1
    db.refresh(game)
    assert game.state == SessionState.ABANDONED


def test_refused(
    client: TestClient,
    summary: Callable[..., Response],
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    assert_error: Callable[..., None],
) -> None:
    student, other = make_user(), make_user()
    game = make_game_session(student)

    assert_error(summary(other, game.id), 404, ErrorCode.NOT_FOUND)
    assert_error(summary(student, uuid4()), 404, ErrorCode.NOT_FOUND)
    assert_error(summary(make_user(role=UserRole.ADMIN), game.id), 403, ErrorCode.FORBIDDEN_ROLE)
    assert_error(client.get(f"{URL}/{game.id}/summary"), 401, ErrorCode.NOT_AUTHENTICATED)
