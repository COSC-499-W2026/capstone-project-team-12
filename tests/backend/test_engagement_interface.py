"""Tests for the public engagement interface (Issues #11, #15, and #86)."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement.interface import (
    EngagementResult,
    apply_location_result,
    enrolment_ended_by_archive,
    get_class_language,
    is_actively_enrolled,
    is_class_archived,
)
from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.engagement import (
    Class,
    ClassEnrollment,
    PointsLedger,
    UserLanguageStats,
)
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

    db.refresh(enrollment)
    enrollment.is_active = False
    enrollment.end_reason = EnrollmentEndReason.LEFT
    enrollment.left_at = enrollment.joined_at + timedelta(seconds=1)
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
        enrollment.left_at = enrollment.joined_at + timedelta(seconds=1)
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


def test_apply_location_result_awards_completed_location(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    game = make_game_session(student=student)
    location = make_session_location(
        game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    result = apply_location_result(location.id, student.id, db)

    assert result == EngagementResult(
        location_points_awarded=100,
        total_points=100,
        level=1,
        level_up=False,
        streak_days=0,
        challenges_completed=(),
        achievements_unlocked=("first_step",),
    )

    award = db.scalar(
        select(PointsLedger).where(
            PointsLedger.user_id == student.id,
            PointsLedger.source_type == "location",
            PointsLedger.source_ref == str(location.id),
        )
    )
    assert award is not None
    assert award.points == 100
    assert award.language == game.target_language
    assert award.class_id == game.class_id

    stats = db.get(UserLanguageStats, (student.id, game.target_language))
    assert stats is not None
    assert stats.total_points == 100


def test_apply_location_result_does_not_manage_transaction(
    db: Session,
    transaction_calls: list[str],
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    game = make_game_session(student=student)
    location = make_session_location(
        game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    apply_location_result(location.id, student.id, db)

    assert "commit" not in transaction_calls
    assert "rollback" not in transaction_calls


def test_completed_location_awarded_only_once(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    game = make_game_session(student=student)
    location = make_session_location(
        game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    first = apply_location_result(location.id, student.id, db)
    second = apply_location_result(location.id, student.id, db)

    assert first.location_points_awarded == 100
    assert second.location_points_awarded == 0
    assert second.total_points == 100

    awards = db.scalars(
        select(PointsLedger).where(
            PointsLedger.user_id == student.id,
            PointsLedger.source_type == "location",
        )
    ).all()
    assert len(awards) == 1


@pytest.mark.parametrize(
    "status",
    [LocationStatus.FAILED, LocationStatus.SKIPPED],
)
def test_unsuccessful_location_awards_zero(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    status: LocationStatus,
) -> None:
    student = make_user()
    game = make_game_session(student=student)
    location = make_session_location(game, position=1)

    location.status = status
    location.points_awarded = 0
    if status == LocationStatus.SKIPPED:
        from app.models.enums import SkipReason

        location.skip_reason = next(iter(SkipReason))
    db.flush()

    result = apply_location_result(location.id, student.id, db)

    assert result.location_points_awarded == 0
    assert result.total_points == 0
    assert result.level == 1
    assert db.get(UserLanguageStats, (student.id, game.target_language)) is None


@pytest.mark.parametrize(
    ("starting_points", "expected_level"),
    [
        (400, 2),
        (1400, 3),
        (2899, 3),
        (2900, 4),
        (4899, 4),
        (4900, 5),
    ],
)
def test_level_thresholds(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    starting_points: int,
    expected_level: int,
) -> None:
    student = make_user()
    game = make_game_session(student=student)

    db.add(
        UserLanguageStats(
            user_id=student.id,
            language=game.target_language,
            total_points=starting_points,
        )
    )
    db.flush()

    location = make_session_location(
        game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    result = apply_location_result(location.id, student.id, db)

    assert result.total_points == starting_points + 100
    assert result.level == expected_level


def test_location_award_preserves_other_language_totals(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    game = make_game_session(student=student, target_language=Language.JA)

    db.add(
        UserLanguageStats(
            user_id=student.id,
            language=Language.FR,
            total_points=500,
        )
    )
    db.flush()

    location = make_session_location(
        game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    result = apply_location_result(location.id, student.id, db)

    japanese = db.get(UserLanguageStats, (student.id, Language.JA))
    french = db.get(UserLanguageStats, (student.id, Language.FR))

    assert result.total_points == 100
    assert japanese is not None and japanese.total_points == 100
    assert french is not None and french.total_points == 500


def test_location_owned_by_another_student_is_rejected(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    owner = make_user()
    other_student = make_user()
    game = make_game_session(student=owner)
    location = make_session_location(
        game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    with pytest.raises(AppError) as exc:
        apply_location_result(location.id, other_student.id, db)

    assert exc.value.code == ErrorCode.NOT_FOUND
    assert db.get(UserLanguageStats, (other_student.id, game.target_language)) is None


def test_unfinished_location_is_rejected(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    game = make_game_session(student=student)
    location = make_session_location(game, position=1)

    with pytest.raises(AppError) as exc:
        apply_location_result(location.id, student.id, db)

    assert exc.value.code == ErrorCode.INVALID_STAGE
    assert db.get(UserLanguageStats, (student.id, game.target_language)) is None


def test_first_step_badge_is_unlocked_only_once(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    """Retrying the same completed location must not duplicate its badge."""
    from app.models.engagement import Achievement, UserAchievement

    student = make_user()
    game = make_game_session(student=student)
    location = make_session_location(
        game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    first = apply_location_result(location.id, student.id, db)
    second = apply_location_result(location.id, student.id, db)

    assert first.achievements_unlocked == ("first_step",)
    assert second.achievements_unlocked == ()
    assert second.location_points_awarded == 0
    assert second.total_points == 100

    badge = db.scalar(select(Achievement).where(Achievement.achievement_key == "first_step"))
    assert badge is not None

    unlocks = db.scalars(
        select(UserAchievement).where(
            UserAchievement.user_id == student.id,
            UserAchievement.achievement_id == badge.id,
            UserAchievement.language == game.target_language,
        )
    ).all()
    assert len(unlocks) == 1


def test_failed_location_does_not_unlock_first_step(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    """A failed location must not earn points or unlock a badge."""
    from app.models.engagement import UserAchievement

    student = make_user()
    game = make_game_session(student=student)
    location = make_session_location(
        game,
        position=1,
        status=LocationStatus.FAILED,
        points_awarded=0,
    )

    result = apply_location_result(location.id, student.id, db)

    assert result.location_points_awarded == 0
    assert result.achievements_unlocked == ()
    assert (
        db.scalars(select(UserAchievement).where(UserAchievement.user_id == student.id)).all() == []
    )


def test_first_step_badge_is_tracked_per_language(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    """The first completed location in each language earns its own badge."""
    from app.models.engagement import Achievement, UserAchievement
    from app.models.enums import SessionState

    student = make_user()

    # First game: Japanese
    japanese_game = make_game_session(student=student, target_language=Language.JA)
    japanese_location = make_session_location(
        japanese_game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    japanese_result = apply_location_result(japanese_location.id, student.id, db)

    # Close the Japanese session before creating the French session.
    # The database allows only one active or paused game per student.
    japanese_game.state = SessionState.COMPLETED
    japanese_game.completed_at = datetime.now(UTC)
    db.flush()

    # Second game: French
    french_game = make_game_session(student=student, target_language=Language.FR)
    french_location = make_session_location(
        french_game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    french_result = apply_location_result(french_location.id, student.id, db)

    assert japanese_result.achievements_unlocked == ("first_step",)
    assert french_result.achievements_unlocked == ("first_step",)

    badge = db.scalar(select(Achievement).where(Achievement.achievement_key == "first_step"))
    assert badge is not None

    unlocks = db.scalars(
        select(UserAchievement).where(
            UserAchievement.user_id == student.id,
            UserAchievement.achievement_id == badge.id,
        )
    ).all()

    assert len(unlocks) == 2
    assert {unlock.language for unlock in unlocks} == {
        Language.JA,
        Language.FR,
    }
