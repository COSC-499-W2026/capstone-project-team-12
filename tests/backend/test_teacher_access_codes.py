"""Tests for generating additional classroom access codes."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.engagement import AccessCode, Class
from app.models.enums import ClassStaffRole, UserRole
from app.engagement.classes import semester_end_expiration

URL = "/api/v1/teacher/classes"


def test_owner_can_generate_additional_code(
    client: TestClient,
    db: Session,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, teacher)

    response = client.post(
        f"{URL}/{school_class.id}/access-codes",
        json={
            "expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "max_uses": 5,
        },
        headers=auth_headers(teacher),
    )

    assert response.status_code == 201
    data = response.json()
    assert data["class_id"] == str(school_class.id)
    assert len(data["code"]) == 8
    assert data["max_uses"] == 5

    saved = db.get(AccessCode, data["id"])
    assert saved is not None
    assert saved.code == data["code"]
    assert saved.created_by == teacher.id
    assert saved.is_active is True
    assert saved.uses_count == 0


def test_student_account_ta_can_generate_code(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    ta = make_user(role=UserRole.STUDENT)
    school_class = make_class()
    make_class_staff(school_class, ta, role=ClassStaffRole.ASSISTANT)

    response = client.post(
        f"{URL}/{school_class.id}/access-codes",
        json={},
        headers=auth_headers(ta),
    )

    assert response.status_code == 201
    assert response.json()["class_id"] == str(school_class.id)


def test_code_defaults_to_semester_end(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class(semester="2026W1")
    make_class_staff(school_class, teacher)

    response = client.post(
        f"{URL}/{school_class.id}/access-codes",
        json={},
        headers=auth_headers(teacher),
    )

    assert response.status_code == 201
    assert datetime.fromisoformat(response.json()["expires_at"]) == semester_end_expiration(
        "2026W1"
    )


def test_unassigned_student_cannot_generate_code(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
) -> None:
    student = make_user()
    school_class = make_class()

    response = client.post(
        f"{URL}/{school_class.id}/access-codes",
        json={},
        headers=auth_headers(student),
    )

    assert response.status_code == 403


def test_archived_class_rejects_new_code(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class(archived_at=datetime.now(UTC))
    make_class_staff(school_class, teacher)

    response = client.post(
        f"{URL}/{school_class.id}/access-codes",
        json={},
        headers=auth_headers(teacher),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CLASS_ARCHIVED"


def test_missing_class_returns_not_found(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
) -> None:
    teacher = make_user(role=UserRole.STAFF)

    response = client.post(
        f"{URL}/{uuid4()}/access-codes",
        json={},
        headers=auth_headers(teacher),
    )

    assert response.status_code == 404


def test_invalid_max_uses_is_rejected(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, teacher)

    response = client.post(
        f"{URL}/{school_class.id}/access-codes",
        json={"max_uses": 0},
        headers=auth_headers(teacher),
    )

    assert response.status_code == 422


def test_code_generation_requires_authentication(
    client: TestClient,
    make_class: Callable[..., Class],
) -> None:
    school_class = make_class()

    response = client.post(
        f"{URL}/{school_class.id}/access-codes",
        json={},
    )

    assert response.status_code == 401
