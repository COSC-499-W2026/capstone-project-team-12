"""Tests for the public engagement interface (Issues #11 and #15)."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.engagement.interface import (
    EngagementResult,
    apply_location_result,
    get_class_language,
    is_actively_enrolled,
    is_class_archived,
)
from app.models.accounts import User
from app.models.engagement import Class, ClassEnrollment
from app.models.enums import EnrollmentEndReason, Language


def test_get_class_language(db: Session, make_class: Callable[..., Class]) -> None:
    """Return the target language for an existing class."""
    school_class = make_class(target_language=Language.JA)

    assert get_class_language(school_class.id, db) == Language.JA


def test_get_class_language_missing(db: Session) -> None:
    """Return None when the class does not exist."""
    assert get_class_language(uuid4(), db) is None


def test_active_enrolment(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    """Return True when the student has an active enrolment."""
    student = make_user()
    school_class = make_class()
    make_enrollment(student, school_class)

    assert is_actively_enrolled(student.id, school_class.id, db) is True


def test_student_not_enrolled(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
) -> None:
    """Return False when the student has no enrolment."""
    student = make_user()
    school_class = make_class()

    assert is_actively_enrolled(student.id, school_class.id, db) is False


def test_inactive_enrolment(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    """Return False when the student's enrolment is inactive."""
    student = make_user()
    school_class = make_class()
    enrollment = make_enrollment(student, school_class)

    # Retrieve the database-generated joining timestamp before updating.
    db.refresh(enrollment)

    enrollment.is_active = False
    enrollment.end_reason = EnrollmentEndReason.LEFT
    enrollment.left_at = max(datetime.now(UTC), enrollment.joined_at)
    db.flush()

    assert is_actively_enrolled(student.id, school_class.id, db) is False


def test_archived_class(db: Session, make_class: Callable[..., Class]) -> None:
    """Return True when the class has been archived."""
    school_class = make_class()
    school_class.archived_at = datetime.now(UTC)
    db.flush()

    assert is_class_archived(school_class.id, db) is True


def test_active_class(db: Session, make_class: Callable[..., Class]) -> None:
    """Return False when the class has not been archived."""
    school_class = make_class()

    assert is_class_archived(school_class.id, db) is False


def test_missing_class_not_archived(db: Session) -> None:
    """Return False for a missing class."""
    assert is_class_archived(uuid4(), db) is False


def test_engagement_result_fields() -> None:
    """Expose the fields required by the engagement response contract."""
    result = EngagementResult(
        location_points_awarded=100,
        total_points=200,
        level=2,
        level_up=True,
        streak_days=3,
        challenges_completed=("complete_first_location",),
        achievements_unlocked=("first_success",),
    )

    assert result.location_points_awarded == 100
    assert result.total_points == 200
    assert result.level == 2
    assert result.level_up is True
    assert result.streak_days == 3
    assert result.challenges_completed == ("complete_first_location",)
    assert result.achievements_unlocked == ("first_success",)


def test_engagement_result_is_frozen() -> None:
    """Prevent reassignment of response fields."""
    result = EngagementResult(
        location_points_awarded=0,
        total_points=0,
        level=1,
        level_up=False,
        streak_days=0,
        challenges_completed=(),
        achievements_unlocked=(),
    )

    with pytest.raises(FrozenInstanceError):
        result.total_points = 100  # type: ignore[misc]


def test_apply_location_result_returns_placeholder(db: Session) -> None:
    """Return placeholder values until Issue #15 is implemented."""
    result = apply_location_result(
        session_location_id=uuid4(),
        student_id=uuid4(),
        db=db,
    )

    assert result == EngagementResult(
        location_points_awarded=0,
        total_points=0,
        level=1,
        level_up=False,
        streak_days=0,
        challenges_completed=(),
        achievements_unlocked=(),
    )


def test_apply_location_result_repeat_placeholder_is_non_awarding(
    db: Session,
) -> None:
    """Confirm placeholder responses award nothing; idempotency is unimplemented."""
    session_location_id = uuid4()
    student_id = uuid4()

    first_result = apply_location_result(
        session_location_id=session_location_id,
        student_id=student_id,
        db=db,
    )
    second_result = apply_location_result(
        session_location_id=session_location_id,
        student_id=student_id,
        db=db,
    )

    assert first_result.location_points_awarded == 0
    assert second_result.location_points_awarded == 0
    assert second_result.level_up is False
    assert second_result.challenges_completed == ()
    assert second_result.achievements_unlocked == ()


def test_apply_location_result_does_not_manage_transaction(
    db: Session,
    transaction_calls: list[str],
) -> None:
    """Ensure the interface stub does not commit or roll back."""
    apply_location_result(
        session_location_id=uuid4(),
        student_id=uuid4(),
        db=db,
    )

    assert "commit" not in transaction_calls
    assert "rollback" not in transaction_calls
