"""Integration tests for PATCH /api/v1/teacher/classes/{class_id}."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.errors import ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, UserRole

URL = "/api/v1/teacher/classes"


def test_owner_updates_class_name(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)
    original_language = school_class.target_language

    response = client.patch(
        f"{URL}/{school_class.id}",
        json={"name": "JAPN 102 - Updated"},
        headers=auth_headers(owner),
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "JAPN 102 - Updated"

    db.refresh(school_class)
    assert school_class.name == "JAPN 102 - Updated"
    assert school_class.target_language == original_language


def test_owner_disables_leaderboard(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)

    response = client.patch(
        f"{URL}/{school_class.id}",
        json={"leaderboard_enabled": False},
        headers=auth_headers(owner),
    )

    assert response.status_code == 200, response.text
    assert response.json()["leaderboard_enabled"] is False

    db.refresh(school_class)
    assert school_class.leaderboard_enabled is False


def test_owner_updates_both_settings(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)
    original_language = school_class.target_language
    original_semester = school_class.semester

    response = client.patch(
        f"{URL}/{school_class.id}",
        json={
            "name": "Updated Classroom",
            "leaderboard_enabled": False,
        },
        headers=auth_headers(owner),
    )

    assert response.status_code == 200, response.text

    body = response.json()
    assert body["class_id"] == str(school_class.id)
    assert body["name"] == "Updated Classroom"
    assert body["leaderboard_enabled"] is False
    assert body["target_language"] == original_language
    assert body["semester"] == original_semester

    db.refresh(school_class)
    assert school_class.name == "Updated Classroom"
    assert school_class.leaderboard_enabled is False
    assert school_class.target_language == original_language
    assert school_class.semester == original_semester


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("language", "fr"),
        ("target_language", "fr"),
        ("semester", "2027W1"),
        ("name", "   "),
        ("name", ""),
        ("name", "x" * 256),
    ],
)
def test_invalid_settings_rejected(
    field: str,
    value: str,
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)

    original_name = school_class.name
    original_language = school_class.target_language
    original_semester = school_class.semester

    response = client.patch(
        f"{URL}/{school_class.id}",
        json={field: value},
        headers=auth_headers(owner),
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR.value

    assert school_class.name == original_name
    assert school_class.target_language == original_language
    assert school_class.semester == original_semester


@pytest.mark.parametrize(
    "role, staff_role",
    [
        (UserRole.STUDENT, None),
        (UserRole.STAFF, None),
        (UserRole.STAFF, ClassStaffRole.ASSISTANT),
        (UserRole.STUDENT, ClassStaffRole.ASSISTANT),
    ],
    ids=["student", "other teacher", "staff TA", "student TA"],
)
def test_only_the_owner_can_update_class(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    auth_headers: Callable[[User], dict[str, str]],
    role: UserRole,
    staff_role: ClassStaffRole | None,
) -> None:
    user = make_user(role=role)
    school_class = make_class()
    if staff_role is not None:
        make_class_staff(school_class, user, role=staff_role)

    response = client.patch(
        f"{URL}/{school_class.id}",
        json={"name": "Unauthorized Change"},
        headers=auth_headers(user),
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE.value


def test_archived_class_cannot_be_updated(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class(archived_at=datetime.now(UTC))
    make_class_staff(school_class, owner)
    original_name = school_class.name

    response = client.patch(
        f"{URL}/{school_class.id}",
        json={"name": "Updated"},
        headers=auth_headers(owner),
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == ErrorCode.CLASS_ARCHIVED.value

    assert school_class.name == original_name


def test_unauthenticated_update_rejected(
    client: TestClient,
) -> None:
    response = client.patch(
        f"{URL}/{uuid4()}",
        json={"name": "Updated"},
    )

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED.value


def test_missing_class_returns_404(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    owner = make_user(role=UserRole.STAFF)

    response = client.patch(
        f"{URL}/{uuid4()}",
        json={"name": "Updated Classroom"},
        headers=auth_headers(owner),
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == ErrorCode.NOT_FOUND.value
