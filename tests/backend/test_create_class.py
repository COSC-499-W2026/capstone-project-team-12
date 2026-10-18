"""Tests for classroom creation and validation."""

from collections.abc import Callable
from datetime import UTC, datetime
from time import time
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from jwt import encode
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.accounts import ClassStaff, User
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, UserRole


def authenticate(client: TestClient, user: User) -> None:
    """Authenticate the test client as the supplied user."""
    token = encode(
        {"sub": str(user.id), "exp": int(time()) + 3600},
        str(get_settings().access_token_secret_key),
        "HS256",
    )
    client.headers["Authorization"] = f"Bearer {token}"


def class_payload(
    *,
    name: str = "Japanese 101",
    semester: str | None = None,
) -> dict[str, str]:
    """Return a valid classroom creation payload by default."""
    if semester is None:
        semester = f"{datetime.now(UTC).year + 1}W1"

    return {
        "name": name,
        "semester": semester,
        "target_language": "ja",
    }


def test_staff_can_create_class(
    client: TestClient,
    make_user: Callable[..., User],
) -> None:
    """Allow authorized staff to create a classroom."""
    teacher = make_user(role=UserRole.STAFF)
    authenticate(client, teacher)

    response = client.post(
        "/api/v1/teacher/classes",
        json=class_payload(),
    )

    assert response.status_code == 201, response.text

    data = response.json()
    assert data["name"] == "Japanese 101"
    assert data["target_language"] == "ja"
    assert len(data["access_code"]) == 8


def test_student_cannot_create_class(
    client: TestClient,
    make_user: Callable[..., User],
) -> None:
    """Reject classroom creation by a student."""
    student = make_user(role=UserRole.STUDENT)
    authenticate(client, student)

    response = client.post(
        "/api/v1/teacher/classes",
        json=class_payload(),
    )

    assert response.status_code == 403, response.text


def test_expired_semester_is_rejected(
    client: TestClient,
    make_user: Callable[..., User],
) -> None:
    """Reject creation for a semester that has already ended."""
    teacher = make_user(role=UserRole.STAFF)
    authenticate(client, teacher)

    response = client.post(
        "/api/v1/teacher/classes",
        json=class_payload(semester="2020W1"),
    )

    assert response.status_code == 422, response.text


def test_creator_is_assigned_owner_role(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
) -> None:
    """Create a ClassStaff owner record for the classroom creator."""
    teacher = make_user(role=UserRole.STAFF)
    authenticate(client, teacher)

    response = client.post(
        "/api/v1/teacher/classes",
        json=class_payload(),
    )

    assert response.status_code == 201, response.text

    class_id = UUID(response.json()["id"])

    school_class = db.get(Class, class_id)
    assert school_class is not None
    assert school_class.name == "Japanese 101"

    owner = (
        db.query(ClassStaff)
        .filter(
            ClassStaff.class_id == class_id,
            ClassStaff.user_id == teacher.id,
        )
        .one_or_none()
    )

    assert owner is not None
    assert owner.role == ClassStaffRole.OWNER


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "A" * 101,
    ],
)
def test_invalid_class_names_are_rejected(
    client: TestClient,
    make_user: Callable[..., User],
    name: str,
) -> None:
    """Reject empty, whitespace-only, and excessively long names."""
    teacher = make_user(role=UserRole.STAFF)
    authenticate(client, teacher)

    response = client.post(
        "/api/v1/teacher/classes",
        json=class_payload(name=name),
    )

    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "semester",
    [
        "0000S1",
        "9999W1",
        "2026W3",
        "invalid",
    ],
)
def test_invalid_semesters_are_rejected(
    client: TestClient,
    make_user: Callable[..., User],
    semester: str,
) -> None:
    """Reject invalid semester years and formats."""
    teacher = make_user(role=UserRole.STAFF)
    authenticate(client, teacher)

    response = client.post(
        "/api/v1/teacher/classes",
        json=class_payload(semester=semester),
    )

    assert response.status_code == 422, response.text
