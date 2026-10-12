"""Tests for access code validation and class enrolment (Issue #106)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.engagement.interface import validate_code_and_enrol
from app.errors import AppError, ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import AccessCode, Class, ClassEnrollment
from app.models.enums import ClassStaffRole, EnrollmentEndReason, UserRole


def test_valid_code_enrols_student(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    student = make_user()
    school_class = make_class()
    code = make_access_code(school_class)

    result = validate_code_and_enrol(student.id, code.code, db)

    enrollment = db.scalar(
        select(ClassEnrollment).where(
            ClassEnrollment.student_id == student.id,
            ClassEnrollment.class_id == school_class.id,
        )
    )

    assert result == school_class.id
    assert enrollment is not None
    assert enrollment.is_active is True
    assert enrollment.access_code_id == code.id
    assert code.uses_count == 1


def test_code_is_case_and_separator_insensitive(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    student = make_user()
    school_class = make_class()
    make_access_code(school_class, code="K7PM3QXD")

    result = validate_code_and_enrol(student.id, " k7pm-3qxd ", db)

    assert result == school_class.id


@pytest.mark.parametrize("invalid_code", ["", "INVALID!", "UNKNOWN1"])
def test_invalid_code_is_rejected(
    db: Session,
    make_user: Callable[..., User],
    invalid_code: str,
) -> None:
    student = make_user()

    with pytest.raises(AppError) as exc:
        validate_code_and_enrol(student.id, invalid_code, db)

    assert exc.value.code == ErrorCode.INVALID_ACCESS_CODE


@pytest.mark.parametrize(
    "overrides",
    [
        {"is_active": False},
        {"expires_at": datetime.now(UTC) - timedelta(days=1)},
        {"max_uses": 1, "uses_count": 1},
    ],
)
def test_unusable_code_is_rejected(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    overrides: dict[str, object],
) -> None:
    student = make_user()
    school_class = make_class()
    code = make_access_code(school_class, **overrides)

    with pytest.raises(AppError) as exc:
        validate_code_and_enrol(student.id, code.code, db)

    assert exc.value.code == ErrorCode.INVALID_ACCESS_CODE


def test_staff_cannot_enrol(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    staff = make_user(role=UserRole.STAFF)
    school_class = make_class()
    code = make_access_code(school_class)

    with pytest.raises(AppError) as exc:
        validate_code_and_enrol(staff.id, code.code, db)

    assert exc.value.code == ErrorCode.FORBIDDEN_ROLE


def test_deactivated_student_cannot_enrol(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    student = make_user()
    student.is_active = False
    db.flush()

    school_class = make_class()
    code = make_access_code(school_class)

    with pytest.raises(AppError) as exc:
        validate_code_and_enrol(student.id, code.code, db)

    assert exc.value.code == ErrorCode.ACCOUNT_DISABLED


def test_archived_class_is_rejected(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    student = make_user()
    school_class = make_class(archived_at=datetime.now(UTC))
    code = make_access_code(school_class)

    with pytest.raises(AppError) as exc:
        validate_code_and_enrol(student.id, code.code, db)

    assert exc.value.code == ErrorCode.CLASS_ARCHIVED


def test_ta_cannot_enrol_in_assigned_class(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    make_class_staff: Callable[..., ClassStaff],
) -> None:
    student = make_user()
    school_class = make_class()
    code = make_access_code(school_class)
    make_class_staff(school_class, student, role=ClassStaffRole.ASSISTANT)

    with pytest.raises(AppError) as exc:
        validate_code_and_enrol(student.id, code.code, db)

    assert exc.value.code == ErrorCode.TA_SAME_CLASS_CONFLICT


def test_second_class_in_same_language_is_rejected(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    student = make_user()
    first_class = make_class()
    second_class = make_class()
    make_enrollment(student, first_class)
    code = make_access_code(second_class)

    with pytest.raises(AppError) as exc:
        validate_code_and_enrol(student.id, code.code, db)

    assert exc.value.code == ErrorCode.ALREADY_IN_CLASS_FOR_LANGUAGE


def test_previously_removed_student_cannot_rejoin(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    student = make_user()
    school_class = make_class()
    code = make_access_code(school_class)
    enrollment = make_enrollment(student, school_class, access_code_id=code.id)

    enrollment.is_active = False
    enrollment.end_reason = EnrollmentEndReason.REMOVED
    enrollment.left_at = enrollment.joined_at + timedelta(seconds=1)
    db.flush()

    with pytest.raises(AppError) as exc:
        validate_code_and_enrol(student.id, code.code, db)

    assert exc.value.code == ErrorCode.REMOVED_FROM_CLASS


def test_previously_left_student_can_rejoin(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    student = make_user()
    school_class = make_class()
    code = make_access_code(school_class)
    enrollment = make_enrollment(student, school_class, access_code_id=code.id)
    original_id = enrollment.id

    enrollment.is_active = False
    enrollment.end_reason = EnrollmentEndReason.LEFT
    enrollment.left_at = enrollment.joined_at + timedelta(seconds=1)
    db.flush()

    result = validate_code_and_enrol(student.id, code.code, db)

    count = db.scalar(
        select(func.count())
        .select_from(ClassEnrollment)
        .where(
            ClassEnrollment.student_id == student.id,
            ClassEnrollment.class_id == school_class.id,
        )
    )

    assert result == school_class.id
    assert enrollment.id == original_id
    assert enrollment.is_active is True
    assert enrollment.end_reason is None
    assert enrollment.left_at is None
    assert count == 1
