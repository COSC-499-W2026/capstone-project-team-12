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

    This structure follows the engagement response contract in the
    technical specification. It represents the expected result of the
    engagement operation; the current Issue #15 implementation is a stub.

    Attributes:
        location_points_awarded: Points awarded for this specific location.
            A completed location awards 100 points; a failed or skipped
            location awards zero points.
        total_points: Cumulative points for the student in the relevant
            language, including eligible engagement bonuses.
        level: Current level derived from cumulative points for the relevant
            language.
        level_up: Whether processing this location caused a level increase.
        streak_days: Current global streak in days across all languages.
        challenges_completed: Identifiers of daily challenges newly
            completed by processing this location.
        achievements_unlocked: Identifiers of achievements newly unlocked
            by processing this location or its resulting streak change.

    Notes:
        Achievements award badges but no points. Location, daily challenge,
        and streak milestone points are tracked through the points ledger.
    """

    location_points_awarded: int
    total_points: int
    level: int
    level_up: bool
    streak_days: int
    challenges_completed: tuple[str, ...]
    achievements_unlocked: tuple[str, ...]


def apply_location_result(
    session_location_id: UUID,
    student_id: UUID,
    db: Session,
) -> EngagementResult:
    """Process a finished session location and return engagement information.

    Interface contract for Issue #15. The scoring and persistence
    implementation has not yet been implemented by this stub.

    The caller must set the session location's final status before invoking
    this function within the finish-location transaction.

    Location scoring:
        - A completed MATCH location awards 100 points.
        - A failed or skipped location awards zero points.
        - Failed and skipped locations do not count as active days for
          streaks.
        - A location's points are attributed to its session's target
          language and class. The class is null for Practice sessions.

    Points and levels:
        Points are tracked separately for each language. The points ledger
        is the source of truth for awards. Each award and the corresponding
        user_language_stats.total_points update must occur in the same
        transaction.

        Levels are derived from cumulative points per language:
        - Level 1: 0-499 points.
        - Level 2: 500-1,499 points.
        - Level 3: 1,500-2,999 points.
        - Level 4: 3,000-4,999 points.
        - Level 5: 5,000 or more points.

        The caller must use location_points_awarded to update
        session_locations.points_awarded and game_sessions.total_score
        within the same transaction, without independently calculating
        the location award.

    Idempotency:
        Processing must be safe to repeat. A unique ledger entry for each
        user, award source, and source reference must prevent duplicate
        awards. Repeated calls must not award the same location, daily
        challenge, or streak milestone points more than once.

        A repeated call must not report a new level-up, newly completed
        challenges, or newly unlocked achievements from the same processing
        event. It must return the current cumulative engagement state.

    Streaks:
        Streaks are global across languages and use the America/Vancouver
        calendar day. Completing a location on the same day does not
        increment the streak again. Completing a location on the day
        immediately after the previous active day increments the streak.
        Otherwise, the current streak starts at one. The best streak is
        the maximum current streak reached.

    Daily challenges:
        Three challenges are generated per user per local day when first
        requested. Challenge progress is updated when a location finishes.
        Challenges are global across languages and expire at local midnight.
        Completion and difficulty challenges award 50 or 100 points.

    Streak milestones:
        The 7-day milestone awards 100 points, and the 30-day milestone
        awards 300 points. Each milestone is awarded once per user.
        Streak milestone and daily challenge awards must use separate,
        uniquely identifiable ledger entries.

    Achievements:
        Achievements award zero points. They are checked when a location
        finishes and when the streak changes.

    Validation:
        The implementation must verify that the session location exists,
        belongs to student_id, and has reached a final status before
        processing it. Appropriate application errors must be used for
        invalid states, based on the project's established error contract.

    Transactions:
        The caller owns the transaction. Engagement may add records and
        flush pending database changes, but must not commit or roll back.
        All related location, session, ledger, and engagement-stat changes
        must be consistent within the finish-location transaction.

    This stub returns placeholder values without reading or modifying
    the database. It does not yet implement validation, scoring, persistence,
    ledger entries, level calculation, streak updates, challenge progress,
    achievement checks, or idempotency enforcement.

    Args:
        session_location_id: ID of the session location to process.
        student_id: ID of the student who owns the session location.
        db: SQLAlchemy session belonging to the caller's transaction.

    Returns:
        EngagementResult containing location-specific points and cumulative
        engagement information. Values are placeholders until implemented.

    Raises:
        AppError: The eventual implementation will raise an appropriate
            application error if the location does not exist, belongs to
            another student, or has not reached a final status.
    """
    return EngagementResult(
        location_points_awarded=0,
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


def is_actively_enrolled(
    student_id: UUID,
    class_id: UUID,
    db: Session,
) -> bool:
    """Return whether a student has an active enrolment in the class.

    Only active enrolments for the specified student and class count.
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

    Returns False if the class does not exist. Check existence separately
    when the caller needs to distinguish a missing class from an active one.
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

    The access code must exist, be active, not be expired, and have
    remaining uses. The user must exist, be active, and have the student
    role. The target class must not be archived, the student must not be
    its teaching assistant, and the student must not already have an active
    enrolment in another class for the same language.

    Input is normalized by removing whitespace and hyphens and converting
    to uppercase.

    An existing enrolment row is reused when possible. Its original joining
    date is preserved. A student previously removed from the target class
    cannot rejoin through this function.

    The access-code use count is incremented atomically. The function flushes
    pending changes but does not commit; the caller owns the transaction.

    Raises:
        AppError: With INVALID_ACCESS_CODE if the code is unknown, inactive,
            expired, exhausted, or references a missing class.
        AppError: With ACCOUNT_DISABLED if the user account is deactivated.
        AppError: With FORBIDDEN_ROLE if the user does not exist or is not
            a student.
        AppError: With CLASS_ARCHIVED if the target class is archived.
        AppError: With TA_SAME_CLASS_CONFLICT if the student assists the
            target class.
        AppError: With ALREADY_IN_CLASS_FOR_LANGUAGE if the student already
            has an active enrolment for the same language.
        AppError: With REMOVED_FROM_CLASS if the student was removed from
            the target class.
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
