"""Tests for classroom access-code management."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from jwt import encode
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.accounts import User
from app.models.engagement import ACCESS_CODE_ALPHABET, AccessCode, Class
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


def future_semester() -> str:
    """Return a semester that has not ended."""
    return f"{datetime.now(UTC).year + 1}W1"


def semester_expiry(semester: str) -> datetime:
    """Return the expected UTC expiration for a W1 semester."""
    return datetime(int(semester[:4]) + 1, 1, 1, tzinfo=UTC)


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
    semester = future_semester()
    school_class = make_class(semester=semester)
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
    assert stored.expires_at == semester_expiry(semester)


def test_unassigned_student_cannot_create_code(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
) -> None:
    student = make_user()
    school_class = make_class(semester=future_semester())
    authenticate(client, student)

    response = client.post(
        f"/api/v1/teacher/classes/{school_class.id}/access-codes",
        json={},
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
        semester=future_semester(),
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
    school_class = make_class(semester=future_semester())
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
    school_class = make_class(semester=future_semester())
    make_class_staff(school_class, teacher)
    authenticate(client, teacher)

    response = client.post(
        f"/api/v1/teacher/classes/{school_class.id}/access-codes",
        json={
            "expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_timezone_naive_expiration_is_rejected(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class(semester=future_semester())
    make_class_staff(school_class, teacher)
    authenticate(client, teacher)

    response = client.post(
        f"/api/v1/teacher/classes/{school_class.id}/access-codes",
        json={"expires_at": "2027-01-01T12:00:00"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_duplicate_access_code_is_retried(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_access_code: Callable[..., AccessCode],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class(semester=future_semester())
    make_class_staff(school_class, teacher)

    existing_code = make_access_code(
        school_class,
        created_by=teacher.id,
    )
    authenticate(client, teacher)

    replacement_code = "ABCDEFGH"
    assert replacement_code != existing_code.code
    assert all(character in ACCESS_CODE_ALPHABET for character in replacement_code)

    with patch(
        "app.engagement.access_code_management.generate_access_code",
        side_effect=[existing_code.code, replacement_code],
    ) as mocked_generator:
        response = client.post(
            f"/api/v1/teacher/classes/{school_class.id}/access-codes",
            json={},
        )

    assert response.status_code == 201, response.text
    assert response.json()["code"] == replacement_code
    assert mocked_generator.call_count == 2

    stored = db.get(AccessCode, response.json()["id"])
    assert stored is not None
    assert stored.code == replacement_code


def test_unauthenticated_user_cannot_create_code(
    client: TestClient,
    make_class: Callable[..., Class],
) -> None:
    """Access-code creation requires authentication."""
    client.headers.pop("Authorization", None)
    school_class = make_class(semester=future_semester())

    response = client.post(
        f"/api/v1/teacher/classes/{school_class.id}/access-codes",
        json={},
    )

    assert response.status_code == 401
