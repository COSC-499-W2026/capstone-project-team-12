"""Tests for the POST /api/v1/me/classes/join endpoint."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jwt import encode
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import AccessCode, Class, ClassEnrollment
from app.models.enums import (
    ClassStaffRole,
    EnrollmentEndReason,
    Language,
    UserRole,
)


def authenticate(client: TestClient, user: User) -> None:
    """Authenticate with a real JWT access token."""
    token = encode(
        {
            "sub": str(user.id),
            "exp": int(time()) + 3600,
        },
        str(get_settings().access_token_secret_key),
        "HS256",
    )
    client.headers["Authorization"] = f"Bearer {token}"


def join_as(client: TestClient, user: User, code: str) -> Any:
    """Submit a join request authenticated as the given user."""
    authenticate(client, user)

    return client.post(
        "/api/v1/me/classes/join",
        json={"access_code": code},
    )


def assert_error(response: Any, status: int, code: ErrorCode) -> None:
    """Verify the API returns the expected structured error."""
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code.value


def test_student_joins_class_without_changing_practice_language(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    """Joining selects the class without changing Practice language."""
    student = make_user(preferred_language=Language.FR)
    school_class = make_class(target_language=Language.JA)
    access_code = make_access_code(school_class)

    response = join_as(client, student, access_code.code)

    assert response.status_code == 200, response.text
    assert response.json()["class_id"] == str(school_class.id)
    assert response.json()["language"] == Language.JA.value

    db.refresh(student)
    db.refresh(access_code)

    assert student.current_class_id == school_class.id
    assert student.preferred_language == Language.FR
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


def test_missing_token_returns_401(
    client: TestClient,
) -> None:
    """Reject a join request without an access token."""
    response = client.post(
        "/api/v1/me/classes/join",
        json={"access_code": "K7PM-3QXD"},
    )

    assert_error(response, 401, ErrorCode.NOT_AUTHENTICATED)


def test_invalid_token_returns_401(
    client: TestClient,
) -> None:
    """Reject a join request with an invalid access token."""
    client.headers["Authorization"] = "Bearer invalid-token"

    response = client.post(
        "/api/v1/me/classes/join",
        json={"access_code": "K7PM-3QXD"},
    )

    assert_error(response, 401, ErrorCode.NOT_AUTHENTICATED)


def test_disabled_account_cannot_join(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
) -> None:
    """Reject authentication for a disabled account."""
    student = make_user()
    student.is_active = False
    db.flush()

    response = join_as(client, student, "K7PM-3QXD")

    assert_error(response, 403, ErrorCode.ACCOUNT_DISABLED)


def test_invalid_access_code(
    client: TestClient,
    make_user: Callable[..., User],
) -> None:
    """An invalid access code returns HTTP 400."""
    student = make_user()

    response = join_as(client, student, "INVALIDCODE")

    assert_error(response, 400, ErrorCode.INVALID_ACCESS_CODE)


def test_lowercase_hyphenated_access_code_is_accepted(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    """Accept the lowercase, hyphenated code format in the documentation."""
    student = make_user()
    school_class = make_class()
    access_code = make_access_code(school_class)

    formatted_code = (f"{access_code.code[:4]}-{access_code.code[4:]}").lower()

    response = join_as(client, student, formatted_code)

    assert response.status_code == 200, response.text
    assert response.json()["class_id"] == str(school_class.id)


def test_expired_access_code_is_rejected(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    """An expired code cannot be used to join."""
    student = make_user()
    school_class = make_class()
    access_code = make_access_code(school_class)
    access_code.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()

    response = join_as(client, student, access_code.code)

    assert_error(response, 400, ErrorCode.INVALID_ACCESS_CODE)


def test_exhausted_access_code_is_rejected(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    """A code that has reached its usage limit cannot be used."""
    student = make_user()
    school_class = make_class()
    access_code = make_access_code(school_class)
    access_code.max_uses = 1
    access_code.uses_count = 1
    db.flush()

    response = join_as(client, student, access_code.code)

    assert_error(response, 400, ErrorCode.INVALID_ACCESS_CODE)


def test_archived_class_cannot_be_joined(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    """A valid access code cannot enrol a student in an archived class."""
    student = make_user()
    school_class = make_class()
    access_code = make_access_code(school_class)
    school_class.archived_at = datetime.now(UTC)
    db.flush()

    response = join_as(client, student, access_code.code)

    assert_error(response, 409, ErrorCode.CLASS_ARCHIVED)


def test_cannot_join_second_active_class_in_same_language(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    """A student cannot join two active classes in the same language."""
    student = make_user()

    first_class = make_class(target_language=Language.JA)
    second_class = make_class(target_language=Language.JA)

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

    assert response.status_code == 200, response.text
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

    assert response.status_code == 200, response.text
    assert response.json()["class_id"] == str(school_class.id)

    db.refresh(enrollment)

    assert enrollment.id == original_enrollment_id
    assert enrollment.is_active is True
    assert enrollment.end_reason is None
    assert enrollment.left_at is None


def test_retry_after_successful_join_returns_same_class(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    """Retrying a successful join does not consume another code use."""
    student = make_user()
    school_class = make_class()
    access_code = make_access_code(school_class)

    first_response = join_as(client, student, access_code.code)
    assert first_response.status_code == 200, first_response.text

    db.refresh(access_code)
    uses_after_first_join = access_code.uses_count

    second_response = join_as(client, student, access_code.code)

    assert second_response.status_code == 200, second_response.text
    assert second_response.json()["class_id"] == str(school_class.id)

    db.refresh(access_code)
    assert access_code.uses_count == uses_after_first_join


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


def test_oversized_access_code_is_rejected(
    client: TestClient,
    make_user: Callable[..., User],
) -> None:
    """Reject access-code inputs longer than 32 characters."""
    student = make_user()

    response = join_as(client, student, "A" * 33)

    assert_error(response, 422, ErrorCode.VALIDATION_ERROR)
