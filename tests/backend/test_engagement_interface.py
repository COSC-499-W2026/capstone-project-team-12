"""Tests for the public class and enrolment lookup interface (Issue #11)."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.engagement.interface import (
    enrolment_ended_by_archive,
    get_class_language,
    is_actively_enrolled,
    is_class_archived,
)
from app.models.accounts import User
from app.models.engagement import Class, ClassEnrollment
from app.models.enums import EnrollmentEndReason, Language


def test_get_class_language(db: Session, make_class: Callable[..., Class]) -> None:
    school_class = make_class(target_language=Language.JA)

    assert get_class_language(school_class.id, db) == Language.JA


def test_get_class_language_missing(db: Session) -> None:
    assert get_class_language(uuid4(), db) is None


def test_active_enrolment(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    student = make_user()
    school_class = make_class()
    make_enrollment(student, school_class)

    assert is_actively_enrolled(student.id, school_class.id, db) is True


def test_student_not_enrolled(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
) -> None:
    student = make_user()
    school_class = make_class()

    assert is_actively_enrolled(student.id, school_class.id, db) is False


def test_inactive_enrolment(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    student = make_user()
    school_class = make_class()
    enrollment = make_enrollment(student, school_class)

    enrollment.is_active = False
    enrollment.end_reason = EnrollmentEndReason.LEFT
    enrollment.left_at = datetime.now(UTC)
    db.flush()

    assert is_actively_enrolled(student.id, school_class.id, db) is False


def test_archived_class(db: Session, make_class: Callable[..., Class]) -> None:
    school_class = make_class()
    school_class.archived_at = datetime.now(UTC)
    db.flush()

    assert is_class_archived(school_class.id, db) is True


def test_active_class(db: Session, make_class: Callable[..., Class]) -> None:
    school_class = make_class()

    assert is_class_archived(school_class.id, db) is False


def test_missing_class_not_archived(db: Session) -> None:
    assert is_class_archived(uuid4(), db) is False


@pytest.mark.parametrize(
    ("end_reason", "expected"),
    [
        (EnrollmentEndReason.ARCHIVED, True),
        (EnrollmentEndReason.LEFT, False),
        (EnrollmentEndReason.REMOVED, False),
        (None, False),  # still enrolled
    ],
)
def test_enrolment_ended_by_archive(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    end_reason: EnrollmentEndReason | None,
    expected: bool,
) -> None:
    student, school_class = make_user(), make_class()
    ended = (
        {}
        if end_reason is None
        else {"is_active": False, "end_reason": end_reason, "left_at": datetime.now(UTC)}
    )
    make_enrollment(student, school_class, **ended)

    assert enrolment_ended_by_archive(student.id, school_class.id, db) is expected


def test_never_enrolled_student_did_not_end_by_archive(
    db: Session, make_user: Callable[..., User], make_class: Callable[..., Class]
) -> None:
    assert enrolment_ended_by_archive(make_user().id, make_class().id, db) is False
