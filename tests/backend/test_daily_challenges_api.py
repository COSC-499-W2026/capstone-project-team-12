"""Tests for GET /api/v1/me/daily-challenges (A13)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement.interface import (
    EngagementResult,
    apply_location_result,
    ensure_daily_challenges,
)
from app.models.accounts import User
from app.models.engagement import DailyChallenge
from app.models.enums import Difficulty, Language, UserRole
from app.models.game import GameSession, SessionLocation

URL = "/api/v1/me/daily-challenges"


def test_student_receives_three_daily_challenges(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)

    response = client.get(URL, headers=auth_headers(student))

    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["challenges"]) == 3
    assert [item["slot"] for item in data["challenges"]] == [1, 2, 3]
    assert all(item["progress_current"] == 0 for item in data["challenges"])
    assert all(item["completed"] is False for item in data["challenges"])
    assert all(item["challenge_date"] == data["challenge_date"] for item in data["challenges"])

    persisted = db.scalars(select(DailyChallenge).where(DailyChallenge.user_id == student.id)).all()
    assert len(persisted) == 3


def test_daily_challenges_are_persisted_between_requests(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)
    headers = auth_headers(student)

    first = client.get(URL, headers=headers)
    second = client.get(URL, headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert [item["id"] for item in first.json()["challenges"]] == [
        item["id"] for item in second.json()["challenges"]
    ]


def test_challenges_are_private_to_each_student(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    first = make_user(role=UserRole.STUDENT)
    second = make_user(role=UserRole.STUDENT)

    first_response = client.get(URL, headers=auth_headers(first))
    second_response = client.get(URL, headers=auth_headers(second))

    assert first_response.status_code == 200, first_response.text
    assert second_response.status_code == 200, second_response.text
    first_ids = {item["id"] for item in first_response.json()["challenges"]}
    second_ids = {item["id"] for item in second_response.json()["challenges"]}
    assert first_ids.isdisjoint(second_ids)


def test_admin_cannot_access_daily_challenges(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    admin = make_user(role=UserRole.ADMIN)

    response = client.get(URL, headers=auth_headers(admin))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_unauthenticated_request_is_rejected(client: TestClient) -> None:
    response = client.get(URL)
    assert response.status_code == 401


@pytest.fixture
def a13_play(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    freeze_clock: Callable[..., None],
) -> tuple[User, datetime, Callable[[int, datetime], EngagementResult]]:
    student = make_user()
    game = make_game_session(student=student, difficulty=Difficulty.BASIC)
    midnight = datetime(2026, 7, 2, tzinfo=ZoneInfo("America/Vancouver")).astimezone(UTC)

    def complete(position: int, now: datetime) -> EngagementResult:
        freeze_clock(now, "app.engagement.streaks", "app.engagement.challenge_progress")
        location = make_session_location(game, position)
        return apply_location_result(
            location.id,
            student.id,
            completed=True,
            target_language=Language.JA,
            class_id=game.class_id,
            db=db,
        )

    return student, midnight, complete


def test_consecutive_local_days_extend_streak_and_award_reward(
    a13_play: tuple[User, datetime, Callable[[int, datetime], EngagementResult]],
) -> None:
    _, midnight, complete = a13_play
    first = complete(1, midnight - timedelta(minutes=1))
    assert first.streak_days == 1
    assert first.total_points == first.location_points_awarded + 50
    assert len(first.challenges_completed) == 1
    assert complete(2, midnight).streak_days == 2


def test_same_day_keeps_streak_and_expired_challenge_stays_incomplete(
    a13_play: tuple[User, datetime, Callable[[int, datetime], EngagementResult]],
    db: Session,
) -> None:
    student, midnight, complete = a13_play
    before = midnight - timedelta(minutes=1)
    assert complete(1, before).streak_days == complete(2, before).streak_days == 1
    yesterday = ensure_daily_challenges(student.id, db, now=before)[1]
    assert yesterday.progress_current == 2
    assert str(yesterday.id) not in complete(3, midnight).challenges_completed
    db.refresh(yesterday)
    assert yesterday.progress_current == 2
    assert yesterday.completed_at is None
