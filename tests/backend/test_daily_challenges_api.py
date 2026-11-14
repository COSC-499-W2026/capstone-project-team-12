"""Tests for GET /api/v1/me/daily-challenges (A13)."""

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.engagement import DailyChallenge
from app.models.enums import UserRole

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
