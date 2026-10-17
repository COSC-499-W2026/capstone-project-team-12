"""Tests for classroom access-code management."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user
from app.models.accounts import User
from app.models.engagement import ACCESS_CODE_ALPHABET, AccessCode, Class
from app.models.enums import ClassStaffRole, UserRole


def authenticate(client: TestClient, user: User) -> None:
    client.app.dependency_overrides[get_current_user] = lambda: user


@pytest.mark.parametrize(
    ("user_role", "class_role"),
    [
        (UserRole.STAFF, ClassStaffRole.OWNER),
        (UserRole.STUDENT, ClassStaffRole.ASSISTANT),
    ],
)
def test_owner_and_ta_can_create_codes(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    user_role: UserRole,
    class_role: ClassStaffRole,
) -> None:
    user = make_user(role=user_role)
    school_class = make_class(semester="2027W1")
    make_class_staff(school_class, user, role=class_role)
    authenticate(client, user)

    response = client.post(
        f"/api/v1/teacher/classes/{school_class.id}/access-codes",
        json={"max_uses": 3},
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert len(data["code"]) == 8
    assert all(character in ACCESS_CODE_ALPHABET for character in data["code"])
    assert data["max_uses"] == 3
    assert data["uses_count"] == 0
    assert data["is_active"] is True
    assert data["class_id"] == str(school_class.id)

    stored = db.get(AccessCode, data["id"])
    assert stored is not None
    assert stored.created_by == user.id
    assert stored.expires_at == datetime(2028, 1, 1, tzinfo=UTC)


def test_unassigned_student_cannot_create_code(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
) -> None:
    student = make_user()
    school_class = make_class(semester="2027W1")
    authenticate(client, student)

    response = client.post(
        f"/api/v1/teacher/classes/{school_class.id}/access-codes",
        json={},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_owner_can_deactivate_code(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_access_code: Callable[..., AccessCode],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, teacher)
    code = make_access_code(school_class, created_by=teacher.id)
    authenticate(client, teacher)

    response = client.patch(
        f"/api/v1/teacher/access-codes/{code.id}",
        json={"is_active": False},
    )

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False
    db.refresh(code)
    assert code.is_active is False


def test_unassigned_student_cannot_deactivate_code(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    student = make_user()
    school_class = make_class()
    code = make_access_code(school_class)
    authenticate(client, student)

    response = client.patch(
        f"/api/v1/teacher/access-codes/{code.id}",
        json={"is_active": False},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_archived_class_cannot_create_codes(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class(
        semester="2027W1",
        archived_at=datetime.now(UTC),
    )
    make_class_staff(school_class, teacher)
    authenticate(client, teacher)

    response = client.post(
        f"/api/v1/teacher/classes/{school_class.id}/access-codes",
        json={},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CLASS_ARCHIVED"


def test_invalid_usage_limit_is_rejected(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class(semester="2027W1")
    make_class_staff(school_class, teacher)
    authenticate(client, teacher)

    response = client.post(
        f"/api/v1/teacher/classes/{school_class.id}/access-codes",
        json={"max_uses": 0},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_expired_requested_code_is_rejected(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class(semester="2027W1")
    make_class_staff(school_class, teacher)
    authenticate(client, teacher)

    response = client.post(
        f"/api/v1/teacher/classes/{school_class.id}/access-codes",
        json={"expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat()},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_access_code_returns_404(
    client: TestClient,
    make_user: Callable[..., User],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    authenticate(client, teacher)

    response = client.patch(
        f"/api/v1/teacher/access-codes/{uuid4()}",
        json={"is_active": False},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
