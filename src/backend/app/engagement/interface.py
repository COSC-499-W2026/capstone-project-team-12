"""Public interface of app.engagement: the only module other components may import.

Implemented:
- Issue #11: Class language, active enrolment, and archived-state lookups.
- Issue #106: Access code validation and student enrolment.

Interface stub:
- Issue #15: Location result to engagement.

Callers must check class existence and archived status before enrolment
so archived classes produce CLASS_ARCHIVED rather than an enrolment error.

Engagement and enrolment functions do not commit transactions. Callers are
responsible for committing or rolling back the entire operation.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import AccessCode, Class, ClassEnrollment
from app.models.enums import (
    ClassStaffRole,
    EnrollmentEndReason,
    Language,
    UserRole,
)

__all__ = [
    "EngagementResult",
    "apply_location_result",
    "get_class_language",
    "is_actively_enrolled",
    "is_class_archived",
    "validate_code_and_enrol",
]


@dataclass(frozen=True)
class EngagementResult:
    """Engagement information returned after processing a location result.

    Fields follow the technical specification's engagement response contract.

    Attributes:
        total_points: Student's cumulative points for the relevant language.
        level: Student's current level for the relevant language.
        level_up: Whether this result caused a level increase.
        streak_days: Student's current global streak in days.
        challenges_completed: Challenge identifiers completed by this result.
        achievements_unlocked: Achievement identifiers newly unlocked.
    """

    total_points: int
    level: int
    level_up: bool
    streak_days: int
    challenges_completed: tuple[str, ...]
    achievements_unlocked: tuple[str, ...]


def apply_location_result(
    session_location_id: UUID,
    db: Session,
) -> EngagementResult:
    """Process a finished session location and return engagement information.

    Contract for the eventual implementation:

    The caller must set the session location's final status before invoking
    this function within the finish-location transaction.

    Engagement determines the points awarded from the final status:
    - Completed MATCH locations award 100 points.
    - Failed and skipped locations award zero points.

    Engagement records awarded points in the points ledger and updates the
    student's engagement state, including levels, streaks, challenges,
    and achievements.

    Points and levels are tracked separately for each language.
    Streaks are tracked globally across languages.

    The points ledger is the source of truth. The caller is responsible for
    copying the awarded location points into session_locations.points_awarded
    and updating game_sessions.total_score within the same transaction.

    The exact mechanism for communicating the awarded point amount to the
    caller must be agreed upon before implementing the integration.

    Processing must be idempotent. A unique ledger entry associated with
    each awarded session location must prevent duplicate point awards.

    Repeated calls for the same processed location must return the current
    cumulative totals without awarding points again. On a repeated call,
    level_up must be False and achievements_unlocked must be empty.

    Failed and skipped locations must award zero points and preserve the
    student's existing cumulative points and level. They must return
    level_up=False and must not count as an active day for streaks.

    The eventual implementation must reject unknown session locations,
    locations belonging to another student, and unfinished locations.

    The caller owns the transaction. Engagement may add records and flush
    pending database changes, but must not commit or roll back.

    This temporary stub returns fixed placeholder values without reading
    or modifying the database. It does not yet implement scoring,
    validation, persistence, streak updates, or idempotency enforcement.

    Args:
        session_location_id: ID of the finished session location.
        db: SQLAlchemy session belonging to the caller's transaction.

    Returns:
        EngagementResult containing cumulative points and level for the
        relevant language, global streak days, and completed challenges
        and newly unlocked achievements. The stub returns placeholders.

    Raises:
        AppError: In the eventual implementation, when the session location
            does not exist, belongs to another student, or is unfinished.
            These checks are not implemented by this stub.
    """
    return EngagementResult(
        total_points=0,
        level=1,
        level_up=False,
        streak_days=0,
        challenges_completed=(),
        achievements_unlocked=(),
    )


def get_class_language(class_id: UUID, db: Session) -> Language | None:
    """Return the class language, or None if the class does not exist."""
    school_class = db.get(Class, class_id)
    if school_class is None:
        return None
    return Language(school_class.target_language)


def is_actively_enrolled(student_id: UUID, class_id: UUID, db: Session) -> bool:
    """Return whether a student has an active enrolment in the class.

    Callers must check class existence and archived status first.
    """
    statement = select(ClassEnrollment.id).where(
        ClassEnrollment.student_id == student_id,
        ClassEnrollment.class_id == class_id,
        ClassEnrollment.is_active.is_(True),
    )
    return db.scalar(statement) is not None


def is_class_archived(class_id: UUID, db: Session) -> bool:
    """Return whether a class is archived.

    Returns False if the class does not exist. Check existence separately.
    """
    school_class = db.get(Class, class_id)
    return school_class is not None and school_class.archived_at is not None


def validate_code_and_enrol(
    student_id: UUID,
    code: str,
    db: Session,
) -> UUID:
    """Validate an access code and enrol a student in its class.

    Return the class ID on success.

    Raises:
        INVALID_ACCESS_CODE: Code is unknown, inactive, expired, or used up.
        ACCOUNT_DISABLED: User account is deactivated.
        FORBIDDEN_ROLE: User is missing or is not a student.
        CLASS_ARCHIVED: Class is archived.
        TA_SAME_CLASS_CONFLICT: Student assists the target class.
        ALREADY_IN_CLASS_FOR_LANGUAGE: Student already has an active class
            in the same language.
        REMOVED_FROM_CLASS: Student was removed from this class.

    Input is normalized by removing spaces and hyphens and converting
    to uppercase.

    The function does not commit. The caller owns the transaction.
    """
    normalized_code = "".join(code.upper().split()).replace("-", "")
    now = datetime.now(UTC)

    user = db.get(User, student_id)

    if user is None:
        raise AppError(ErrorCode.FORBIDDEN_ROLE)

    if not user.is_active:
        raise AppError(ErrorCode.ACCOUNT_DISABLED)

    if user.role != UserRole.STUDENT:
        raise AppError(ErrorCode.FORBIDDEN_ROLE)

    access_code = db.scalar(
        select(AccessCode).where(AccessCode.code == normalized_code).with_for_update()
    )

    if (
        access_code is None
        or not access_code.is_active
        or access_code.expires_at <= now
        or (access_code.max_uses is not None and access_code.uses_count >= access_code.max_uses)
    ):
        raise AppError(ErrorCode.INVALID_ACCESS_CODE)

    school_class = db.get(Class, access_code.class_id)
    if school_class is None:
        raise AppError(ErrorCode.INVALID_ACCESS_CODE)

    if school_class.archived_at is not None:
        raise AppError(ErrorCode.CLASS_ARCHIVED)

    assistant = db.scalar(
        select(ClassStaff.user_id).where(
            ClassStaff.user_id == student_id,
            ClassStaff.class_id == school_class.id,
            ClassStaff.role == ClassStaffRole.ASSISTANT,
        )
    )
    if assistant is not None:
        raise AppError(ErrorCode.TA_SAME_CLASS_CONFLICT)

    existing = db.scalar(
        select(ClassEnrollment).where(
            ClassEnrollment.student_id == student_id,
            ClassEnrollment.class_id == school_class.id,
        )
    )

    if existing is not None and existing.end_reason == EnrollmentEndReason.REMOVED:
        raise AppError(ErrorCode.REMOVED_FROM_CLASS)

    active_enrolment = db.scalar(
        select(ClassEnrollment.id).where(
            ClassEnrollment.student_id == student_id,
            ClassEnrollment.language == school_class.target_language,
            ClassEnrollment.is_active.is_(True),
        )
    )
    if active_enrolment is not None:
        raise AppError(ErrorCode.ALREADY_IN_CLASS_FOR_LANGUAGE)

    if existing is not None:
        # Reuse the existing row and preserve the original joining date.
        existing.is_active = True
        existing.end_reason = None
        existing.left_at = None
        existing.access_code_id = access_code.id
    else:
        db.add(
            ClassEnrollment(
                student_id=student_id,
                class_id=school_class.id,
                access_code_id=access_code.id,
                language=school_class.target_language,
                is_active=True,
                end_reason=None,
                left_at=None,
            )
        )

    # Increment atomically without committing the caller's transaction.
    db.execute(
        update(AccessCode)
        .where(AccessCode.id == access_code.id)
        .values(uses_count=AccessCode.uses_count + 1)
    )

    db.flush()
    return school_class.id
