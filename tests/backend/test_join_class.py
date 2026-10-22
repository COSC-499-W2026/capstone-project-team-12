"""Core tests for POST /api/v1/me/classes/join."""

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
from app.models.enums import ClassStaffRole, EnrollmentEndReason, Language, UserRole


def join_as(client: TestClient, user: User, code: str) -> Any:
    """Join using a real JWT."""
    token = encode(
        {"sub": str(user.id), "exp": int(time()) + 3600},
        str(get_settings().access_token_secret_key),
        "HS256",
    )
    client.headers["Authorization"] = f"Bearer {token}"
    return client.post(
        "/api/v1/me/classes/join",
        json={"access_code": code},
    )


def assert_error(response: Any, status: int, code: ErrorCode) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code.value


def test_successful_join(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    """Joining a class preserves the student's Practice language."""
    student = make_user(preferred_language=Language.FR)
    school_class = make_class(target_language=Language.JA)
    access_code = make_access_code(school_class)
    formatted = f"{access_code.code[:4]}-{access_code.code[4:]}".lower()

    response = join_as(client, student, formatted)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "class_id": str(school_class.id),
        "language": Language.JA.value,
    }

    db.refresh(student)
    db.refresh(access_code)

    assert student.current_class_id == school_class.id
    assert student.preferred_language == Language.FR
    assert access_code.uses_count == 1

    enrollment = (
        db.query(ClassEnrollment).filter_by(student_id=student.id, class_id=school_class.id).one()
    )
    assert enrollment.is_active
    assert enrollment.access_code_id == access_code.id


@pytest.mark.parametrize("token", [None, "invalid-token"])
def test_authentication_required(
    client: TestClient,
    token: str | None,
) -> None:
    if token is not None:
        client.headers["Authorization"] = f"Bearer {token}"

    response = client.post(
        "/api/v1/me/classes/join",
        json={"access_code": "K7PM-3QXD"},
    )
    assert_error(response, 401, ErrorCode.NOT_AUTHENTICATED)


def test_disabled_account(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
) -> None:
    student = make_user()
    student.is_active = False
    db.flush()

    assert_error(
        join_as(client, student, "K7PM-3QXD"),
        403,
        ErrorCode.ACCOUNT_DISABLED,
    )


@pytest.mark.parametrize("state", ["unknown", "expired", "exhausted"])
def test_invalid_code_states(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    state: str,
) -> None:
    student = make_user()
    code = "INVALIDCODE"

    if state != "unknown":
        access_code = make_access_code(make_class())
        code = access_code.code

        if state == "expired":
            access_code.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        else:
            access_code.max_uses = 1
            access_code.uses_count = 1
        db.flush()

    assert_error(
        join_as(client, student, code),
        400,
        ErrorCode.INVALID_ACCESS_CODE,
    )


def test_archived_class(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    school_class = make_class()
    access_code = make_access_code(school_class)
    school_class.archived_at = datetime.now(UTC)
    db.flush()

    assert_error(
        join_as(client, make_user(), access_code.code),
        409,
        ErrorCode.CLASS_ARCHIVED,
    )


def test_same_language_conflict(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    student = make_user()
    make_enrollment(student, make_class(target_language=Language.JA))
    access_code = make_access_code(make_class(target_language=Language.JA))

    assert_error(
        join_as(client, student, access_code.code),
        409,
        ErrorCode.ALREADY_IN_CLASS_FOR_LANGUAGE,
    )


@pytest.mark.parametrize(
    ("state", "expected_status"),
    [
        ("valid", 200),
        ("expired", 200),
        ("exhausted", 200),
        ("deactivated", 400),
    ],
)
def test_retry_does_not_consume_code(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    state: str,
    expected_status: int,
) -> None:
    student = make_user()
    school_class = make_class()
    access_code = make_access_code(school_class)

    first = join_as(client, student, access_code.code)
    assert first.status_code == 200, first.text

    db.refresh(access_code)
    original_uses = access_code.uses_count

    if state == "expired":
        access_code.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif state == "exhausted":
        access_code.max_uses = original_uses
    elif state == "deactivated":
        access_code.is_active = False
    db.flush()

    retry = join_as(client, student, access_code.code)
    assert retry.status_code == expected_status, retry.text

    if expected_status == 200:
        assert retry.json()["class_id"] == str(school_class.id)
    else:
        assert_error(retry, 400, ErrorCode.INVALID_ACCESS_CODE)

    db.refresh(access_code)
    assert access_code.uses_count == original_uses


def test_same_class_different_valid_code_conflict(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    """A different valid code cannot be used to rejoin the same class."""
    student = make_user()
    school_class = make_class()
    original_code = make_access_code(school_class)
    other_code = make_access_code(school_class)

    first = join_as(client, student, original_code.code)
    assert first.status_code == 200, first.text

    response = join_as(client, student, other_code.code)
    assert_error(response, 409, ErrorCode.ALREADY_IN_CLASS_FOR_LANGUAGE)

    db.refresh(original_code)
    db.refresh(other_code)
    assert original_code.uses_count == 1
    assert other_code.uses_count == 0

    enrollment = (
        db.query(ClassEnrollment).filter_by(student_id=student.id, class_id=school_class.id).one()
    )
    assert enrollment.access_code_id == original_code.id


@pytest.mark.parametrize("code_state", ["expired", "exhausted"])
@pytest.mark.parametrize("class_state", ["archived", "same_language_conflict"])
def test_unavailable_code_rejected_before_class_state(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    make_enrollment: Callable[..., ClassEnrollment],
    code_state: str,
    class_state: str,
) -> None:
    """An unavailable code must not expose class or enrolment state."""
    student = make_user()
    school_class = make_class(target_language=Language.JA)
    access_code = make_access_code(school_class)

    if class_state == "archived":
        school_class.archived_at = datetime.now(UTC)
    else:
        make_enrollment(student, make_class(target_language=Language.JA))

    if code_state == "expired":
        access_code.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    else:
        access_code.max_uses = 1
        access_code.uses_count = 1

    db.flush()

    assert_error(
        join_as(client, student, access_code.code),
        400,
        ErrorCode.INVALID_ACCESS_CODE,
    )


def test_removed_student_cannot_rejoin(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    """A student removed by a teacher cannot rejoin using an access code."""
    student = make_user()
    school_class = make_class()
    access_code = make_access_code(school_class)
    enrollment = make_enrollment(student, school_class)

    enrollment.is_active = False
    enrollment.end_reason = EnrollmentEndReason.REMOVED
    enrollment.left_at = enrollment.joined_at + timedelta(minutes=1)
    db.flush()

    assert_error(
        join_as(client, student, access_code.code),
        409,
        ErrorCode.REMOVED_FROM_CLASS,
    )


def test_voluntarily_left_student_can_rejoin(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    """A student who voluntarily left can rejoin their former class."""
    student = make_user(preferred_language=Language.FR)
    school_class = make_class(target_language=Language.JA)
    access_code = make_access_code(school_class)
    enrollment = make_enrollment(student, school_class)

    enrollment.is_active = False
    enrollment.end_reason = EnrollmentEndReason.LEFT
    enrollment.left_at = enrollment.joined_at + timedelta(minutes=1)
    db.flush()

    response = join_as(client, student, access_code.code)

    assert response.status_code == 200, response.text

    db.refresh(enrollment)
    db.refresh(student)

    assert enrollment.is_active
    assert enrollment.end_reason is None
    assert enrollment.left_at is None
    assert student.current_class_id == school_class.id
    assert student.preferred_language == Language.FR


@pytest.mark.parametrize("code_state", ["expired", "exhausted"])
def test_voluntarily_left_student_cannot_rejoin_with_unavailable_code(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    make_enrollment: Callable[..., ClassEnrollment],
    code_state: str,
) -> None:
    """A student who left must use a currently valid access code to rejoin."""
    student = make_user()
    school_class = make_class()
    access_code = make_access_code(school_class)
    enrollment = make_enrollment(student, school_class)

    enrollment.is_active = False
    enrollment.end_reason = EnrollmentEndReason.LEFT
    enrollment.left_at = enrollment.joined_at + timedelta(minutes=1)

    if code_state == "expired":
        access_code.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    else:
        access_code.max_uses = 1
        access_code.uses_count = 1

    db.flush()

    response = join_as(client, student, access_code.code)

    assert_error(response, 400, ErrorCode.INVALID_ACCESS_CODE)

    db.refresh(enrollment)
    assert not enrollment.is_active
    assert enrollment.end_reason == EnrollmentEndReason.LEFT


def test_teacher_cannot_join_as_student(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    """A staff account cannot join a classroom as a student."""
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class()
    access_code = make_access_code(school_class)

    assert_error(
        join_as(client, teacher, access_code.code),
        403,
        ErrorCode.FORBIDDEN_ROLE,
    )


def test_ta_cannot_join_assisted_class(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    make_class_staff: Callable[..., ClassStaff],
) -> None:
    """A teaching assistant cannot enrol in the class they assist."""
    student = make_user()
    school_class = make_class()
    access_code = make_access_code(school_class)

    make_class_staff(
        school_class,
        student,
        role=ClassStaffRole.ASSISTANT,
    )

    assert_error(
        join_as(client, student, access_code.code),
        409,
        ErrorCode.TA_SAME_CLASS_CONFLICT,
    )


def test_ta_can_join_another_class_in_same_language(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    make_class_staff: Callable[..., ClassStaff],
) -> None:
    """A TA can join another class in the same language as a student."""
    student = make_user(preferred_language=Language.FR)

    assisted_class = make_class(target_language=Language.JA)
    target_class = make_class(target_language=Language.JA)
    access_code = make_access_code(target_class)

    make_class_staff(
        assisted_class,
        student,
        role=ClassStaffRole.ASSISTANT,
    )

    response = join_as(client, student, access_code.code)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "class_id": str(target_class.id),
        "language": Language.JA.value,
    }

    db.refresh(student)
    assert student.current_class_id == target_class.id
    assert student.preferred_language == Language.FR
