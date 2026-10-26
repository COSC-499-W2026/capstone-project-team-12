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
    enrolment_ended_by_archive,
    get_class_language,
    is_actively_enrolled,
    is_class_archived,
)
from app.models.accounts import User
from app.models.engagement import Class, ClassEnrollment
from app.models.enums import EnrollmentEndReason, Language, LocationStatus
from app.models.game import GameSession, SessionLocation


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
    enrollment.left_at = max(datetime.now(UTC), enrollment.joined_at)
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
        # still enrolled
        (None, False),
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
    enrollment = make_enrollment(student, school_class)

    if end_reason is not None:
        db.refresh(enrollment)
        enrollment.is_active = False
        enrollment.end_reason = end_reason
        enrollment.left_at = max(datetime.now(UTC), enrollment.joined_at)
        db.flush()

    assert enrolment_ended_by_archive(student.id, school_class.id, db) is expected


def test_never_enrolled_student_did_not_end_by_archive(
    db: Session, make_user: Callable[..., User], make_class: Callable[..., Class]
) -> None:
    assert enrolment_ended_by_archive(make_user().id, make_class().id, db) is False


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


def test_apply_location_result_returns_placeholder(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    """Verify the stub response using a completed location owned by the student."""
    student = make_user()
    game = make_game_session(student=student)
    location = make_session_location(
        game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    result = apply_location_result(
        session_location_id=location.id,
        student_id=student.id,
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


def test_apply_location_result_does_not_manage_transaction(
    db: Session,
    transaction_calls: list[str],
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    """Ensure the interface stub does not commit or roll back."""
    student = make_user()
    game = make_game_session(student=student)
    location = make_session_location(
        game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    apply_location_result(
        session_location_id=location.id,
        student_id=student.id,
        db=db,
    )

    assert "commit" not in transaction_calls
    assert "rollback" not in transaction_calls
