"""Tests for teacher classroom creation."""

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.accounts import ClassStaff, User
from app.models.engagement import (
    ACCESS_CODE_ALPHABET,
    ACCESS_CODE_LENGTH,
    AccessCode,
    Class,
)
from app.models.enums import ClassStaffRole, UserRole


def authenticate(client: TestClient, user: User) -> None:
    """Override authentication for the specified test user."""
    from app.accounts.interface import get_current_user

    client.app.dependency_overrides[get_current_user] = lambda: user


def test_staff_can_create_class(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    authenticate(client, teacher)

    response = client.post(
        "/api/v1/teacher/classes",
        json={
            "name": "JAPN 101 - Fall 2026",
            "semester": "2026W1",
            "target_language": "ja",
        },
    )

    assert response.status_code == 201, response.text

    data = response.json()
    assert data["name"] == "JAPN 101 - Fall 2026"
    assert data["semester"] == "2026W1"
    assert data["target_language"] == "ja"

    code = data["access_code"]
    assert len(code) == ACCESS_CODE_LENGTH
    assert all(character in ACCESS_CODE_ALPHABET for character in code)

    school_class = db.get(Class, data["id"])
    assert school_class is not None
    assert school_class.target_language == "ja"

    owner = db.get(ClassStaff, (school_class.id, teacher.id))
    assert owner is not None
    assert owner.role == ClassStaffRole.OWNER

    access_code = db.query(AccessCode).filter_by(code=code).one()
    assert access_code.class_id == school_class.id
    assert access_code.created_by == teacher.id
    assert access_code.is_active is True
    assert access_code.max_uses is None
    assert access_code.uses_count == 0
    assert access_code.expires_at == datetime(2027, 1, 1, tzinfo=UTC)


def test_student_cannot_create_class(
    client: TestClient,
    make_user: Callable[..., User],
) -> None:
    student = make_user(role=UserRole.STUDENT)
    authenticate(client, student)

    response = client.post(
        "/api/v1/teacher/classes",
        json={
            "name": "JAPN 101",
            "semester": "2026W1",
            "target_language": "ja",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_invalid_language_is_rejected(
    client: TestClient,
    make_user: Callable[..., User],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    authenticate(client, teacher)

    response = client.post(
        "/api/v1/teacher/classes",
        json={
            "name": "Test Class",
            "semester": "2026W1",
            "target_language": "invalid",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_semester_is_rejected(
    client: TestClient,
    make_user: Callable[..., User],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    authenticate(client, teacher)

    response = client.post(
        "/api/v1/teacher/classes",
        json={
            "name": "Test Class",
            "semester": "Fall 2026",
            "target_language": "ja",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_completed_semester_is_rejected(client, make_user):
    """A classroom cannot be created for an already-ended semester."""
    from app.accounts.interface import get_current_user
    from app.models.enums import UserRole

    teacher = make_user(role=UserRole.STAFF)
    client.app.dependency_overrides[get_current_user] = lambda: teacher

    response = client.post(
        "/api/v1/teacher/classes",
        json={
            "name": "Old Japanese Class",
            "semester": "2020W1",
            "target_language": "ja",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
