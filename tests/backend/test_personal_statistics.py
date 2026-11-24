"""Signed-in player statistics by language and difficulty."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.models.accounts import User
from app.models.engagement import Class
from app.models.enums import (
    Difficulty,
    Language,
    LocationStatus,
    SessionState,
    SkipReason,
    UserRole,
)
from app.models.game import GameSession, SessionLocation

URL = "/api/v1/me/statistics"
NOW = datetime(2026, 11, 20, 18, tzinfo=UTC)


def test_my_statistics_groups_own_class_and_practice_results(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user()
    school_class = make_class()
    practice = make_game_session(student)
    classroom = make_game_session(
        student, class_id=school_class.id, state=SessionState.COMPLETED, completed_at=NOW
    )

    for game, seconds in ((practice, 120), (classroom, 2400)):
        make_session_location(
            game,
            1,
            status=LocationStatus.COMPLETED,
            points_awarded=100,
            started_at=NOW - timedelta(seconds=seconds),
            completed_at=NOW,
        )
    make_session_location(practice, 2, status=LocationStatus.FAILED)
    make_session_location(
        practice, 3, status=LocationStatus.SKIPPED, skip_reason=SkipReason.SHUFFLED
    )
    make_session_location(practice, 3, status=LocationStatus.PENDING)
    make_session_location(
        classroom, 2, status=LocationStatus.COMPLETED, points_awarded=100, completed_at=NOW
    )

    for language, difficulty in (
        (Language.FR, Difficulty.BASIC),
        (Language.JA, Difficulty.CHALLENGING),
    ):
        game = make_game_session(
            student,
            target_language=language,
            difficulty=difficulty,
            state=SessionState.COMPLETED,
            completed_at=NOW,
        )
        make_session_location(game, 1, status=LocationStatus.FAILED)
    outsider = make_game_session(make_user())
    make_session_location(outsider, 1, status=LocationStatus.FAILED)

    response = client.get(URL, headers=auth_headers(student))
    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "language": "fr",
            "difficulty": 1,
            "locations_completed": 0,
            "completion_rate": 0.0,
            "average_time_seconds": None,
        },
        {
            "language": "ja",
            "difficulty": 1,
            "locations_completed": 3,
            "completion_rate": 75.0,
            "average_time_seconds": 960.0,
        },
        {
            "language": "ja",
            "difficulty": 3,
            "locations_completed": 0,
            "completion_rate": 0.0,
            "average_time_seconds": None,
        },
    ]


@pytest.mark.parametrize(
    ("role", "status"),
    [(None, 401), (UserRole.ADMIN, 403), (UserRole.STAFF, 200), (UserRole.STUDENT, 200)],
)
def test_my_statistics_requires_a_player_and_returns_empty_without_results(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
    role: UserRole | None,
    status: int,
) -> None:
    headers = {} if role is None else auth_headers(make_user(role=role))
    response = client.get(URL, headers=headers)
    assert response.status_code == status, response.text
    if status == 200:
        assert response.json() == []
