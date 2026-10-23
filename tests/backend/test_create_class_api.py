"""Integration tests for POST /api/v1/teacher/classes."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import (
    ACCESS_CODE_ALPHABET,
    ACCESS_CODE_LENGTH,
    AccessCode,
    Class,
)
from app.models.enums import ClassStaffRole, UserRole

URL = "/api/v1/teacher/classes"


def payload(**overrides: Any) -> dict[str, Any]:
    """Return a valid classroom creation request."""
    values: dict[str, Any] = {
        "name": "JAPN 101 - Fall 2026",
        "semester": "2026W1",
        "language": "ja",
        "expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
    }
    return values | overrides


def test_teacher_creates_class_with_initial_code(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    teacher = make_user(role=UserRole.STAFF)

    response = client.post(
        URL,
        json=payload(max_uses=25),
        headers=auth_headers(teacher),
    )

    assert response.status_code == 201, response.text

    body = response.json()
    class_id = UUID(body["class_id"])

    assert body["name"] == "JAPN 101 - Fall 2026"
    assert body["semester"] == "2026W1"
    assert body["language"] == "ja"
    assert len(body["access_code"]) == ACCESS_CODE_LENGTH
    assert set(body["access_code"]).issubset(set(ACCESS_CODE_ALPHABET))

    school_class = db.get(Class, class_id)
    assert school_class is not None
    assert school_class.target_language == "ja"

    owner = db.scalar(
        select(ClassStaff).where(
            ClassStaff.class_id == class_id,
            ClassStaff.user_id == teacher.id,
        )
    )
    assert owner is not None
    assert owner.role == ClassStaffRole.OWNER

    access_code = db.scalar(
        select(AccessCode).where(
            AccessCode.class_id == class_id,
        )
    )
    assert access_code is not None
    assert access_code.code == body["access_code"]
    assert access_code.created_by == teacher.id
    assert access_code.max_uses == 25
    assert access_code.is_active
    assert access_code.uses_count == 0


def test_student_cannot_create_class(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)

    response = client.post(
        URL,
        json=payload(),
        headers=auth_headers(student),
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE.value
    assert db.scalars(select(Class)).all() == []


def test_unauthenticated_request_rejected(
    client: TestClient,
) -> None:
    response = client.post(URL, json=payload())

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED.value


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("name", "   "),
        ("semester", "2026W3"),
        ("semester", "invalid"),
        ("language", "invalid"),
        ("max_uses", 0),
        ("max_uses", -1),
    ],
)
def test_invalid_request_rejected(
    field: str,
    value: Any,
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    teacher = make_user(role=UserRole.STAFF)

    response = client.post(
        URL,
        json=payload(**{field: value}),
        headers=auth_headers(teacher),
    )

    assert response.status_code == 422, response.text
    assert db.scalars(select(Class)).all() == []


def test_expired_code_rolls_back_class_creation(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    teacher = make_user(role=UserRole.STAFF)

    response = client.post(
        URL,
        json=payload(expires_at=(datetime.now(UTC) - timedelta(days=1)).isoformat()),
        headers=auth_headers(teacher),
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR.value

    assert db.scalars(select(Class)).all() == []
    assert db.scalars(select(ClassStaff)).all() == []
    assert db.scalars(select(AccessCode)).all() == []


def test_naive_expiration_rolls_back_creation(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    naive = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=30)

    response = client.post(
        URL,
        json=payload(expires_at=naive.isoformat()),
        headers=auth_headers(teacher),
    )

    assert response.status_code == 422, response.text
    assert db.scalars(select(Class)).all() == []
    assert db.scalars(select(AccessCode)).all() == []


def test_disabled_teacher_cannot_create_class(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    teacher = make_user(role=UserRole.STAFF, is_active=False)

    response = client.post(
        URL,
        json=payload(),
        headers=auth_headers(teacher),
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == ErrorCode.ACCOUNT_DISABLED.value
    assert db.scalars(select(Class)).all() == []


def test_teacher_creates_class_with_default_expiration(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    teacher = make_user(role=UserRole.STAFF)

    request = payload()
    request.pop("expires_at")

    response = client.post(
        URL,
        json=request,
        headers=auth_headers(teacher),
    )

    assert response.status_code == 201, response.text

    class_id = UUID(response.json()["class_id"])
    access_code = db.scalar(select(AccessCode).where(AccessCode.class_id == class_id))

    assert access_code is not None
    assert access_code.expires_at == datetime(2027, 1, 1, 7, tzinfo=UTC)
