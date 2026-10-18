"""Tests for classroom updates and language immutability."""

from collections.abc import Callable
from datetime import UTC, datetime
from time import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jwt import encode
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.accounts import User
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, UserRole


def authenticate(client: TestClient, user: User) -> None:
    """Authenticate using a real JWT access token."""
    token = encode(
        {
            "sub": str(user.id),
            "exp": int(time()) + 3600,
        },
        str(get_settings().access_token_secret_key),
        "HS256",
    )
    client.headers["Authorization"] = f"Bearer {token}"


def test_owner_can_update_class(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class(target_language="ja")
    make_class_staff(school_class, teacher, role=ClassStaffRole.OWNER)
    authenticate(client, teacher)

    response = client.patch(
        f"/api/v1/teacher/classes/{school_class.id}",
        json={
            "name": "Updated Japanese Class",
            "leaderboard_enabled": False,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Updated Japanese Class"
    assert response.json()["leaderboard_enabled"] is False
    assert response.json()["target_language"] == "ja"

    db.refresh(school_class)
    assert school_class.name == "Updated Japanese Class"
    assert school_class.target_language == "ja"
    assert school_class.leaderboard_enabled is False


def test_owner_can_update_name_only(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class(target_language="ja")
    make_class_staff(school_class, teacher, role=ClassStaffRole.OWNER)
    authenticate(client, teacher)

    original_leaderboard = school_class.leaderboard_enabled

    response = client.patch(
        f"/api/v1/teacher/classes/{school_class.id}",
        json={"name": "Renamed Japanese Class"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Renamed Japanese Class"
    assert response.json()["leaderboard_enabled"] == original_leaderboard

    db.refresh(school_class)
    assert school_class.name == "Renamed Japanese Class"
    assert school_class.leaderboard_enabled == original_leaderboard


def test_owner_can_update_leaderboard_only(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class(target_language="ja")
    make_class_staff(school_class, teacher, role=ClassStaffRole.OWNER)
    authenticate(client, teacher)

    original_name = school_class.name
    new_leaderboard_value = not school_class.leaderboard_enabled

    response = client.patch(
        f"/api/v1/teacher/classes/{school_class.id}",
        json={"leaderboard_enabled": new_leaderboard_value},
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == original_name
    assert response.json()["leaderboard_enabled"] == new_leaderboard_value

    db.refresh(school_class)
    assert school_class.name == original_name
    assert school_class.leaderboard_enabled == new_leaderboard_value


def test_target_language_cannot_be_changed(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class(target_language="ja")
    make_class_staff(school_class, teacher, role=ClassStaffRole.OWNER)
    authenticate(client, teacher)

    response = client.patch(
        f"/api/v1/teacher/classes/{school_class.id}",
        json={"target_language": "fr"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    db.refresh(school_class)
    assert school_class.target_language == "ja"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": None},
        {"leaderboard_enabled": None},
        {"name": "   "},
        {"name": ""},
        {"name": "A" * 101},
    ],
)
def test_invalid_updates_are_rejected(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    payload: dict[str, object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, teacher, role=ClassStaffRole.OWNER)
    authenticate(client, teacher)

    response = client.patch(
        f"/api/v1/teacher/classes/{school_class.id}",
        json=payload,
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_student_cannot_update_class(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
) -> None:
    student = make_user(role=UserRole.STUDENT)
    school_class = make_class()
    authenticate(client, student)

    response = client.patch(
        f"/api/v1/teacher/classes/{school_class.id}",
        json={"name": "Unauthorized Update"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_ta_cannot_update_class(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    assistant = make_user(role=UserRole.STUDENT)
    school_class = make_class()
    make_class_staff(school_class, assistant, role=ClassStaffRole.ASSISTANT)
    authenticate(client, assistant)

    response = client.patch(
        f"/api/v1/teacher/classes/{school_class.id}",
        json={"name": "TA Update"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_archived_class_cannot_be_updated(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class(archived_at=datetime.now(UTC))
    make_class_staff(school_class, teacher, role=ClassStaffRole.OWNER)
    authenticate(client, teacher)

    response = client.patch(
        f"/api/v1/teacher/classes/{school_class.id}",
        json={"name": "Archived Update"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CLASS_ARCHIVED"


def test_unknown_class_returns_403(
    client: TestClient,
    make_user: Callable[..., User],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    authenticate(client, teacher)

    response = client.patch(
        f"/api/v1/teacher/classes/{uuid4()}",
        json={"name": "Unknown Class"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_unauthenticated_user_cannot_update_class(
    client: TestClient,
) -> None:
    """Updating a classroom requires authentication."""
    client.headers.pop("Authorization", None)

    response = client.patch(
        f"/api/v1/teacher/classes/{uuid4()}",
        json={"name": "Unauthorized Update"},
    )

    assert response.status_code == 401
