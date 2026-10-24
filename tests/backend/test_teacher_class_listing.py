"""Tests for listing teacher and teaching-assistant classrooms."""

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.engagement.interface import list_staff_classes
from app.models.accounts import User
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, UserRole

URL = "/api/v1/teacher/classes"


def test_owner_sees_only_own_classes(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    owned = make_class()
    unrelated = make_class()
    make_class_staff(owned, teacher)

    response = client.get(URL, headers=auth_headers(teacher))

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["class_id"] == str(owned.id)
    assert response.json()[0]["staff_role"] == ClassStaffRole.OWNER
    assert str(unrelated.id) not in str(response.json())


def test_student_account_ta_can_list_assisted_class(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    ta = make_user(role=UserRole.STUDENT)
    school_class = make_class()
    make_class_staff(school_class, ta, role=ClassStaffRole.ASSISTANT)

    response = client.get(URL, headers=auth_headers(ta))

    assert response.status_code == 200
    assert response.json()[0]["class_id"] == str(school_class.id)
    assert response.json()[0]["staff_role"] == ClassStaffRole.ASSISTANT


def test_archived_class_remains_visible(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    archived_at = datetime(2026, 10, 1, tzinfo=UTC)
    school_class = make_class(archived_at=archived_at)
    make_class_staff(school_class, teacher)

    response = client.get(URL, headers=auth_headers(teacher))

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["archived_at"] is not None


def test_user_without_staff_assignments_gets_empty_list(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
) -> None:
    student = make_user()

    response = client.get(URL, headers=auth_headers(student))

    assert response.status_code == 200
    assert response.json() == []


def test_listing_requires_authentication(client: TestClient) -> None:
    response = client.get(URL)

    assert response.status_code == 401


def test_listing_service_does_not_write(
    db: Session,
    transaction_calls: list[str],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, teacher)

    transaction_calls.clear()
    rows = list_staff_classes(teacher.id, db)

    assert len(rows) == 1
    assert rows[0][0].id == school_class.id
    assert transaction_calls == []
