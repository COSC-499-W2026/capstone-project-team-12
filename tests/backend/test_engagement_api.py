"""Tests for GET /api/v1/me/engagement (A12)."""

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.engagement import UserLanguageStats
from app.models.enums import Language, UserRole

URL = "/api/v1/me/engagement"


def test_new_student_starts_at_zero(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)

    response = client.get(
        URL,
        params={"language": Language.JA.value},
        headers=auth_headers(student),
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "language": Language.JA.value,
        "total_points": 0,
        "level": 1,
    }


def test_points_are_separate_by_language(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)

    db.add(
        UserLanguageStats(
            user_id=student.id,
            language=Language.JA,
            total_points=600,
        )
    )
    db.add(
        UserLanguageStats(
            user_id=student.id,
            language=Language.ZH,
            total_points=1700,
        )
    )
    db.flush()

    japanese = client.get(
        URL,
        params={"language": Language.JA.value},
        headers=auth_headers(student),
    )
    chinese = client.get(
        URL,
        params={"language": Language.ZH.value},
        headers=auth_headers(student),
    )

    assert japanese.status_code == 200, japanese.text
    assert chinese.status_code == 200, chinese.text
    assert japanese.json()["total_points"] == 600
    assert japanese.json()["level"] == 2
    assert chinese.json()["total_points"] == 1700
    assert chinese.json()["level"] == 3


def test_students_cannot_read_another_students_points(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    first = make_user(role=UserRole.STUDENT)
    second = make_user(role=UserRole.STUDENT)

    db.add(
        UserLanguageStats(
            user_id=first.id,
            language=Language.JA,
            total_points=5000,
        )
    )
    db.flush()

    response = client.get(
        URL,
        params={"language": Language.JA.value},
        headers=auth_headers(second),
    )

    assert response.status_code == 200, response.text
    assert response.json()["total_points"] == 0
    assert response.json()["level"] == 1


def test_unauthenticated_request_is_rejected(client: TestClient) -> None:
    response = client.get(URL, params={"language": Language.JA.value})

    assert response.status_code == 401


def test_non_student_is_forbidden(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    teacher = make_user(role=UserRole.STAFF)

    response = client.get(
        URL,
        params={"language": Language.JA.value},
        headers=auth_headers(teacher),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_ROLE"
