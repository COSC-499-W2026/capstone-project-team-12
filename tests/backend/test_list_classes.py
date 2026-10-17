"""Tests for listing teacher and TA classrooms."""

from collections.abc import Callable

from fastapi.testclient import TestClient

from app.accounts.interface import get_current_user
from app.models.accounts import User
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, UserRole


def authenticate(client: TestClient, user: User) -> None:
    client.app.dependency_overrides[get_current_user] = lambda: user


def test_owner_can_list_owned_class(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, teacher, role=ClassStaffRole.OWNER)
    authenticate(client, teacher)

    response = client.get("/api/v1/teacher/classes")

    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == str(school_class.id)
    assert response.json()[0]["staff_role"] == "owner"


def test_ta_can_list_assigned_class(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    assistant = make_user(role=UserRole.STUDENT)
    school_class = make_class()
    make_class_staff(school_class, assistant, role=ClassStaffRole.ASSISTANT)
    authenticate(client, assistant)

    response = client.get("/api/v1/teacher/classes")

    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == str(school_class.id)
    assert response.json()[0]["staff_role"] == "assistant"


def test_unassigned_classes_are_hidden(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    owned_class = make_class()
    unrelated_class = make_class()

    make_class_staff(owned_class, teacher, role=ClassStaffRole.OWNER)
    authenticate(client, teacher)

    response = client.get("/api/v1/teacher/classes")

    assert response.status_code == 200, response.text

    returned_ids = {item["id"] for item in response.json()}

    assert str(owned_class.id) in returned_ids
    assert str(unrelated_class.id) not in returned_ids


def test_unassigned_student_receives_empty_list(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
) -> None:
    student = make_user(role=UserRole.STUDENT)
    make_class()
    authenticate(client, student)

    response = client.get("/api/v1/teacher/classes")

    assert response.status_code == 200, response.text
    assert response.json() == []
