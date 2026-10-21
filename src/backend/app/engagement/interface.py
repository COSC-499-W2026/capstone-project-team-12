"""Public interface of app.engagement: the only module other components may import.

Implemented:
- Issue #11: Class language, active enrolment, and archived-state lookups.
- Issue #106: Access code validation and student enrolment.
- Issue #84: Classroom creation service.

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

from app.engagement.classes import (
    create_class,
    generate_access_code as generate_access_code,
)
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
    "create_class",
    "enrolment_ended_by_archive",
    "get_class_language",
    "is_actively_enrolled",
    "is_class_archived",
    "validate_code_and_enrol",
]


@dataclass(frozen=True)
class EngagementResult:
    """Engagement information returned after processing a location result.

    Defines the engagement response contract for Issue #15.
    The current implementation is a placeholder.

    Attributes:
        location_points_awarded: New location points awarded by this call.
            A newly processed COMPLETED location awards 100 points.
            FAILED, SKIPPED, and repeated calls award zero new points.
        total_points: Current cumulative points in the target language,
            including eligible engagement bonuses.
        level: Current level derived from cumulative language points.
        level_up: Whether this call newly increased the student's level.
        streak_days: Current global streak across all languages.
        challenges_completed: Daily challenge identifiers newly completed
            during this call.
        achievements_unlocked: Achievement identifiers newly unlocked
            during this call.

    Notes:
        Achievements award badges but no points. Location, daily challenge,
        and streak milestone points use separate points ledger entries.
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

    Interface contract for Issue #15. Scoring and persistence are not yet
    implemented by this stub.

    Ownership:
        student_id identifies the authenticated student making the request.
        The implementation must resolve the student through the location's
        game session and verify that it matches student_id.

        A mismatch raises NOT_FOUND before any engagement changes.
        The signature and ownership check require agreement from the
        finish-location and match/failed-location callers.

    Preconditions:
        The caller must set the session location's final status before
        invoking this function within the finish-location transaction.

    Location scoring:
        - A location with status COMPLETED awards 100 points when first
          processed.
        - A location with status FAILED or SKIPPED awards zero points.
        - FAILED and SKIPPED return existing cumulative language points,
          derived level, and global streak unchanged, with level_up=False
          and no newly completed challenges or unlocked achievements.
        - FAILED and SKIPPED do not count as active days for streaks.
        - Location points are attributed to the session's target language
          and class. The class is null for Practice sessions.

    Points and levels:
        Engagement determines eligible point awards and writes the
        corresponding points ledger entries.

        The points ledger is the source of truth. Each award and the
        corresponding user_language_stats.total_points update must occur
        within the same transaction.

        Levels are derived from cumulative language points, not stored:
        - Level 1: 0-499 points.
        - Level 2: 500-1,499 points.
        - Level 3: 1,500-2,999 points.
        - Level 4: 3,000-4,999 points.
        - Level 5: 5,000 or more points.

    Session score ownership:
        Engagement returns location_points_awarded as the number of new
        location points awarded during this invocation.

        The game caller owns updates to session_locations.points_awarded
        and game_sessions.total_score.

        On first processing, the caller stores the location award.
        On repeat processing, location_points_awarded is zero, but the
        caller must preserve any previously stored location points.

        The caller must recalculate game_sessions.total_score from
        persisted session location scores within the same transaction.
        It must not blindly add the returned award to an existing score.

        Daily challenge and streak milestone bonuses affect engagement
        totals, not the location-only game session score.

    Idempotency:
        Processing must be safe to repeat. Each ledger award must have
        a unique combination of student, award source, and source reference.

        A repeated call must:
        - Award zero additional location points.
        - Preserve previously stored location points.
        - Return current cumulative language points and derived level.
        - Return the current global streak.
        - Set level_up=False.
        - Return challenges_completed=().
        - Return achievements_unlocked=().
        - Avoid duplicate location, challenge, and milestone awards.

    Streaks:
        Streaks are global across languages and use the America/Vancouver
        calendar day.

        Multiple completed locations on the same day count as one active
        day. Completing a location on the day immediately after the
        previous active day increments the streak.

        Otherwise, the current streak starts at one. The best streak
        is the maximum current streak reached.

    Daily challenges:
        Three challenges are generated per student per local day.

        Before recording progress for a completed location, the
        implementation must ensure that the student's daily challenges
        exist, even if the student has not opened the challenges screen.

        Challenge initialization must be idempotent so simultaneous
        requests cannot generate duplicate challenges.

        Challenges are global across languages and expire at local
        midnight. Eligible completion and difficulty challenges award
        50 or 100 points according to their definitions.

        Every challenge award must have its own uniquely identifiable
        points ledger entry. Repeated calls must not award it twice.

    Streak milestones:
        The 7-day milestone awards 100 points.
        The 30-day milestone awards 300 points.

        Each milestone is awarded once per student using a separate,
        uniquely identifiable points ledger entry.

    Achievements:
        Achievements award zero points. Eligibility is checked after
        processing a completed location and any resulting streak change.

    Validation:
        The implementation must verify that:
        - The session location exists.
        - Its associated game session belongs to student_id.
        - The location has reached a valid final status.

        Validation failures must not modify engagement state.

    Transactions:
        The caller owns the database transaction.

        Engagement may add records and flush pending database changes
        to enforce constraints or obtain generated identifiers.

        Engagement must not commit or roll back the transaction.

        Location, session, ledger, and engagement-stat changes must
        remain consistent within the caller's transaction.

    Placeholder limitations:
        This stub returns fixed values without accessing the database.
        It does not implement validation, scoring, persistence, ledger
        entries, level calculation, streak updates, daily challenges,
        achievement checks, or idempotency enforcement.

    Args:
        session_location_id: UUID of the finished session location.
        student_id: UUID of the authenticated student owning the session.
        db: SQLAlchemy session belonging to the caller's transaction.

    Returns:
        EngagementResult containing newly awarded location points and
        cumulative engagement information. Values are placeholders
        until the implementation is completed.

    Raises:
        AppError(ErrorCode.NOT_FOUND): Session location does not exist or
            belongs to another student.
        AppError(ErrorCode.INVALID_STAGE): Session location has not
            reached a final status.

        These errors define the future implementation contract.
        The current stub does not raise them.
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


def enrolment_ended_by_archive(student_id: UUID, class_id: UUID, db: Session) -> bool:
    """Return whether the student's enrolment in the class ended because it was archived.

    False for a student who never joined, left, was removed or is still enrolled.
    """
    statement = select(ClassEnrollment.id).where(
        ClassEnrollment.student_id == student_id,
        ClassEnrollment.class_id == class_id,
        ClassEnrollment.end_reason == EnrollmentEndReason.ARCHIVED,
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

    A retry succeeds without consuming another code use when the student
    is already actively enrolled in the class using the same access code.

    Expired or exhausted codes are accepted for this retry only.
    Deactivated codes remain invalid.

    Raises:
        INVALID_ACCESS_CODE: Code is unknown, inactive, expired, or used up
            unless it qualifies for an existing-enrolment retry.
        ACCOUNT_DISABLED: User account is deactivated.
        FORBIDDEN_ROLE: User is missing or is not a student.
        CLASS_ARCHIVED: Class is archived.
        TA_SAME_CLASS_CONFLICT: Student assists the target class.
        ALREADY_IN_CLASS_FOR_LANGUAGE: Student already has an active class
            in the same language and is not retrying with the original code.
        REMOVED_FROM_CLASS: Student was removed from this class.

    Input is normalized by removing spaces and hyphens and converting
    to uppercase.

    The function locks the student row to serialize concurrent joins.
    It does not commit. The caller owns the transaction.
    """
    normalized_code = "".join(code.upper().split()).replace("-", "")
    now = datetime.now(UTC)

    user = db.scalar(select(User).where(User.id == student_id).with_for_update())

    if user is None:
        raise AppError(ErrorCode.FORBIDDEN_ROLE)

    if not user.is_active:
        raise AppError(ErrorCode.ACCOUNT_DISABLED)

    if user.role != UserRole.STUDENT:
        raise AppError(ErrorCode.FORBIDDEN_ROLE)

    access_code = db.scalar(
        select(AccessCode).where(AccessCode.code == normalized_code).with_for_update()
    )

    if access_code is None or not access_code.is_active:
        raise AppError(ErrorCode.INVALID_ACCESS_CODE)

    # Check expiry and usage limits before exposing class or enrolment state.
    # Only an active enrolment using this exact code qualifies for a retry.
    code_unavailable = access_code.expires_at <= now or (
        access_code.max_uses is not None and access_code.uses_count >= access_code.max_uses
    )

    if code_unavailable:
        retry_enrolment = db.scalar(
            select(ClassEnrollment.id).where(
                ClassEnrollment.student_id == student_id,
                ClassEnrollment.class_id == access_code.class_id,
                ClassEnrollment.access_code_id == access_code.id,
                ClassEnrollment.is_active.is_(True),
            )
        )
        if retry_enrolment is None:
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
        select(ClassEnrollment).where(
            ClassEnrollment.student_id == student_id,
            ClassEnrollment.language == school_class.target_language,
            ClassEnrollment.is_active.is_(True),
        )
    )

    if active_enrolment is not None:
        if (
            active_enrolment.class_id == school_class.id
            and active_enrolment.access_code_id == access_code.id
        ):
            return school_class.id

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
