"""Tests for POST /api/v1/me/classes/{class_id}/leave."""

from collections.abc import Callable
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.engagement.interface import leave_class
from app.errors import ErrorCode
from app.models.accounts import User
from app.models.engagement import Class, ClassEnrollment
from app.models.enums import (
    EnrollmentEndReason,
    Language,
    UserRole,
)


def assert_error(response: Any, status: int, code: ErrorCode) -> None:
    """Check the application's standard error response."""
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code.value


def test_leave_active_class(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    auth_headers: Callable[[User], dict[str, str]],
    leave_as: Callable[..., Any],
) -> None:
    student = make_user()
    school_class = make_class()
    enrollment = make_enrollment(student, school_class)
    student.current_class_id = school_class.id
    db.commit()
    response = leave_as(client, student, str(school_class.id), auth_headers)

    assert response.status_code == 204, response.text
    assert response.content == b""
    db.refresh(enrollment)
    db.refresh(student)
    assert enrollment.is_active is False
    assert enrollment.end_reason == EnrollmentEndReason.LEFT
    assert enrollment.left_at is not None
    assert student.current_class_id is None


def test_leave_preserves_other_selected_class(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    auth_headers: Callable[[User], dict[str, str]],
    leave_as: Callable[..., Any],
) -> None:
    student = make_user()
    first_class = make_class(target_language=Language.FR)
    second_class = make_class(target_language=Language.JA)
    first_enrollment = make_enrollment(student, first_class)
    second_enrollment = make_enrollment(student, second_class)
    student.current_class_id = second_class.id
    db.commit()
    response = leave_as(client, student, str(first_class.id), auth_headers)

    assert response.status_code == 204, response.text
    db.refresh(student)
    db.refresh(first_enrollment)
    db.refresh(second_enrollment)
    assert student.current_class_id == second_class.id
    assert first_enrollment.is_active is False
    assert second_enrollment.is_active is True


def test_leave_selects_another_active_class(
    client: TestClient,
    db: Session,
    two_class_student: Any,
    auth_headers: Callable[[User], dict[str, str]],
    leave_as: Callable[..., Any],
) -> None:
    student = two_class_student.student
    first_class = two_class_student.japanese
    second_class = two_class_student.french
    db.commit()
    response = leave_as(client, student, str(first_class.id), auth_headers)

    assert response.status_code == 204, response.text
    db.refresh(student)
    assert student.current_class_id == second_class.id


def test_cannot_leave_class_without_enrollment(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    auth_headers: Callable[[User], dict[str, str]],
    leave_as: Callable[..., Any],
) -> None:
    student = make_user()
    school_class = make_class()
    response = leave_as(client, student, str(school_class.id), auth_headers)
    assert_error(response, 404, ErrorCode.NOT_FOUND)


def test_cannot_leave_unknown_class(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
    leave_as: Callable[..., Any],
) -> None:
    student = make_user()
    response = leave_as(client, student, str(uuid4()), auth_headers)
    assert_error(response, 404, ErrorCode.NOT_FOUND)


def test_cannot_leave_twice(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    auth_headers: Callable[[User], dict[str, str]],
    leave_as: Callable[..., Any],
) -> None:
    student = make_user()
    school_class = make_class()
    make_enrollment(student, school_class)
    first = leave_as(client, student, str(school_class.id), auth_headers)
    second = leave_as(client, student, str(school_class.id), auth_headers)
    assert first.status_code == 204, first.text
    assert_error(second, 404, ErrorCode.NOT_FOUND)


def test_cannot_leave_another_students_class(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    auth_headers: Callable[[User], dict[str, str]],
    leave_as: Callable[..., Any],
) -> None:
    enrolled_student = make_user()
    other_student = make_user()
    school_class = make_class()
    make_enrollment(enrolled_student, school_class)
    response = leave_as(client, other_student, str(school_class.id), auth_headers)
    assert_error(response, 404, ErrorCode.NOT_FOUND)


def test_staff_cannot_leave_as_student(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    auth_headers: Callable[[User], dict[str, str]],
    leave_as: Callable[..., Any],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class()
    response = leave_as(client, teacher, str(school_class.id), auth_headers)
    assert_error(response, 403, ErrorCode.FORBIDDEN_ROLE)


def test_disabled_student_cannot_leave(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    auth_headers: Callable[[User], dict[str, str]],
    leave_as: Callable[..., Any],
) -> None:
    student = make_user(is_active=False)
    school_class = make_class()
    response = leave_as(client, student, str(school_class.id), auth_headers)
    assert_error(response, 403, ErrorCode.ACCOUNT_DISABLED)


def test_leave_requires_authentication(
    client: TestClient,
    make_class: Callable[..., Class],
) -> None:
    school_class = make_class()
    client.headers.pop("Authorization", None)
    response = client.post(f"/api/v1/me/classes/{school_class.id}/leave")
    assert_error(response, 401, ErrorCode.NOT_AUTHENTICATED)


def test_leave_rejects_invalid_class_id(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
    leave_as: Callable[..., Any],
) -> None:
    student = make_user()
    response = leave_as(client, student, "not-a-uuid", auth_headers)
    assert_error(response, 422, ErrorCode.VALIDATION_ERROR)


def test_leave_service_does_not_commit(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    transaction_calls: list[str],
) -> None:
    student = make_user()
    school_class = make_class()
    make_enrollment(student, school_class)
    transaction_calls.clear()
    leave_class(student.id, school_class.id, db)
    assert "commit" not in transaction_calls


def test_leave_rolls_back_when_game_cleanup_fails(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    auth_headers: Callable[[User], dict[str, str]],
    transaction_calls: list[str],
) -> None:
    """A game cleanup failure must not persist the student's departure."""
    school_class = make_class()
    student = make_user(current_class_id=school_class.id)
    enrollment = make_enrollment(student, school_class)
    db.commit()
    transaction_calls.clear()

    with (
        patch(
            "app.api.v1.classes.end_open_sessions",
            side_effect=RuntimeError("Game cleanup failed"),
        ),
        pytest.raises(RuntimeError, match="Game cleanup failed"),
    ):
        leave_as(client, student, str(school_class.id), auth_headers)

    assert "rollback" in transaction_calls
    assert "commit" not in transaction_calls
    db.refresh(enrollment)
    db.refresh(student)
    assert enrollment.is_active is True
    assert enrollment.end_reason is None
    assert enrollment.left_at is None
    assert student.current_class_id == school_class.id
