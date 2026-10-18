"""Tests for the POST /api/v1/me/classes/join endpoint."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user
from app.errors import ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import AccessCode, Class, ClassEnrollment
from app.models.enums import (
    ClassStaffRole,
    EnrollmentEndReason,
    Language,
    UserRole,
)


def join_as(client: TestClient, user: User, code: str) -> Any:
    """Make an authenticated join request as the given user."""
    assert isinstance(client.app, FastAPI)
    client.app.dependency_overrides[get_current_user] = lambda: user

    return client.post(
        "/api/v1/me/classes/join",
        json={"access_code": code},
    )


def assert_error(response: Any, status: int, code: ErrorCode) -> None:
    """Verify the API returns the expected structured error."""
    assert response.status_code == status
    assert response.json()["error"]["code"] == code.value


def test_student_joins_class_without_changing_practice_language(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    """Joining a class selects it without changing Practice language."""
    student = make_user()
    original_practice_language = student.preferred_language

    # Use a different classroom language to detect unwanted changes.
    class_language = next(
        language for language in Language if language.value != original_practice_language
    )

    school_class = make_class(target_language=class_language)
    access_code = make_access_code(school_class)

    response = join_as(client, student, access_code.code)

    assert response.status_code == 200
    assert response.json()["class_id"] == str(school_class.id)
    assert response.json()["language"] == class_language.value

    db.refresh(student)
    db.refresh(access_code)

    assert student.current_class_id == school_class.id
    assert student.preferred_language == original_practice_language
    assert access_code.uses_count == 1

    enrollment = (
        db.query(ClassEnrollment)
        .filter(
            ClassEnrollment.student_id == student.id,
            ClassEnrollment.class_id == school_class.id,
        )
        .one()
    )

    assert enrollment.is_active is True


def test_invalid_access_code(
    client: TestClient,
    make_user: Callable[..., User],
) -> None:
    """An invalid access code returns HTTP 400."""
    student = make_user()

    response = join_as(client, student, "INVALIDCODE")

    assert_error(response, 400, ErrorCode.INVALID_ACCESS_CODE)


def test_cannot_join_second_active_class_in_same_language(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    """A student cannot join two active classes in the same language."""
    student = make_user()

    first_class = make_class()
    second_class = make_class()

    make_enrollment(student, first_class)
    access_code = make_access_code(second_class)

    response = join_as(client, student, access_code.code)

    assert_error(
        response,
        409,
        ErrorCode.ALREADY_IN_CLASS_FOR_LANGUAGE,
    )


def test_removed_student_cannot_rejoin(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    """A teacher-removed student cannot rejoin the same class."""
    student = make_user()
    school_class = make_class()
    access_code = make_access_code(school_class)

    enrollment = make_enrollment(student, school_class)
    enrollment.is_active = False
    enrollment.end_reason = EnrollmentEndReason.REMOVED
    enrollment.left_at = enrollment.joined_at + timedelta(seconds=1)
    db.flush()

    response = join_as(client, student, access_code.code)

    assert_error(response, 409, ErrorCode.REMOVED_FROM_CLASS)


def test_ta_cannot_join_class_they_assist(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    make_class_staff: Callable[..., ClassStaff],
) -> None:
    """A teaching assistant cannot join the class they assist."""
    student_ta = make_user()
    school_class = make_class()
    access_code = make_access_code(school_class)

    make_class_staff(
        school_class,
        student_ta,
        role=ClassStaffRole.ASSISTANT,
    )

    response = join_as(client, student_ta, access_code.code)

    assert_error(response, 409, ErrorCode.TA_SAME_CLASS_CONFLICT)


def test_ta_can_join_another_class_in_same_language(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    make_class_staff: Callable[..., ClassStaff],
) -> None:
    """A TA can join another class in the same language."""
    student_ta = make_user()

    assisted_class = make_class()
    other_class = make_class()

    make_class_staff(
        assisted_class,
        student_ta,
        role=ClassStaffRole.ASSISTANT,
    )

    access_code = make_access_code(other_class)

    response = join_as(client, student_ta, access_code.code)

    assert response.status_code == 200
    assert response.json()["class_id"] == str(other_class.id)


def test_teacher_cannot_join_as_student(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    """Staff accounts cannot enrol as students."""
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class()
    access_code = make_access_code(school_class)

    response = join_as(client, teacher, access_code.code)

    assert_error(response, 403, ErrorCode.FORBIDDEN_ROLE)


def test_student_can_rejoin_after_voluntarily_leaving(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    """A student who voluntarily left can rejoin the same class."""
    student = make_user()
    school_class = make_class()
    access_code = make_access_code(school_class)

    enrollment = make_enrollment(student, school_class)
    original_enrollment_id = enrollment.id

    enrollment.is_active = False
    enrollment.end_reason = EnrollmentEndReason.LEFT
    enrollment.left_at = enrollment.joined_at + timedelta(seconds=1)
    db.flush()

    response = join_as(client, student, access_code.code)

    assert response.status_code == 200
    assert response.json()["class_id"] == str(school_class.id)

    db.refresh(enrollment)

    assert enrollment.id == original_enrollment_id
    assert enrollment.is_active is True
    assert enrollment.end_reason is None
    assert enrollment.left_at is None


@pytest.mark.parametrize(
    ("access_code", "expected_status", "expected_code"),
    [
        ("", 422, ErrorCode.VALIDATION_ERROR),
        ("   ", 400, ErrorCode.INVALID_ACCESS_CODE),
    ],
)
def test_blank_access_code_rejected(
    client: TestClient,
    make_user: Callable[..., User],
    access_code: str,
    expected_status: int,
    expected_code: ErrorCode,
) -> None:
    """Reject empty and whitespace-only codes with their specific errors."""
    student = make_user()

    response = join_as(client, student, access_code)

    assert_error(response, expected_status, expected_code)
