"""Public interface of app.engagement: the only module other components may import.

Implemented:
- Issue #11: Class language, active enrolment, and archived-state lookups.
- Issue #106: Access code validation and student enrolment.
- Issue #84: Classroom creation, access code generation, settings update
 services (update_class_settings), classroom listing via list_staff_classes,
 and code deactivation.
- Issue #85: Student leave-class service and current-class reassignment.

Implemented in part:
- Issue #15: Location scoring and per-language levels.
  Streaks, challenges, and badges are pending.

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
    deactivate_access_code,
    generate_access_code,
    list_staff_classes,
    update_class_settings,
)
from app.errors import AppError, ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import (
    AccessCode,
    Class,
    ClassEnrollment,
    PointsLedger,
    UserLanguageStats,
)
from app.models.enums import (
    ClassStaffRole,
    EnrollmentEndReason,
    Language,
    LocationStatus,
    UserRole,
)
from app.models.game import GameSession, SessionLocation
from app.services.locks import lock_user

__all__ = [
    "EngagementResult",
    "apply_location_result",
    "create_class",
    "deactivate_access_code",
    "enrolment_ended_by_archive",
    "generate_access_code",
    "get_active_class_ids",
    "get_class_language",
    "is_actively_enrolled",
    "is_class_archived",
    "leave_class",
    "list_staff_classes",
    "require_active_enrolment",
    "update_class_settings",
    "validate_code_and_enrol",
]


@dataclass(frozen=True)
class EngagementResult:
    """Engagement information returned after processing a location result.

    Defines the engagement response contract for Issue #15.
    Streaks, challenges, and achievements are pending implementation.

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


def _level_for_points(total_points: int) -> int:
    """Derive the language level from cumulative points."""
    if total_points >= 5000:
        return 5
    if total_points >= 3000:
        return 4
    if total_points >= 1500:
        return 3
    if total_points >= 500:
        return 2
    return 1


def apply_location_result(
    session_location_id: UUID,
    student_id: UUID,
    db: Session,
) -> EngagementResult:
    """Process a finished session location and return engagement information.

    Interface contract for Issue #15. Location scoring, points persistence,
    per-language totals, and level calculation are implemented in this PR.

    Ownership:
        student_id identifies the authenticated student making the request.
        The implementation must resolve the student through the location's
        game session and verify that it matches student_id.
        A mismatch raises NOT_FOUND before any engagement changes.

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

    Pending functionality:
        Streak tracking, daily challenges, streak milestones, and
        achievement unlocking are not implemented in this PR.
        Until implemented, streak_days is zero and both
        challenges_completed and achievements_unlocked are empty.

    Args:
        session_location_id: UUID of the finished session location.
        student_id: UUID of the authenticated student owning the session.
        db: SQLAlchemy session belonging to the caller's transaction.

    Returns:
        EngagementResult containing newly awarded location points and
        cumulative engagement information.

    Raises:
        AppError(ErrorCode.NOT_FOUND): Session location does not exist or
            belongs to another student.
        AppError(ErrorCode.INVALID_STAGE): Session location has not
            reached a final status.
    """
    location = db.get(SessionLocation, session_location_id)
    if location is None:
        raise AppError(ErrorCode.NOT_FOUND)

    game = db.get(GameSession, location.session_id)
    if game is None or game.student_id != student_id:
        raise AppError(ErrorCode.NOT_FOUND)

    if location.status not in (
        LocationStatus.COMPLETED,
        LocationStatus.FAILED,
        LocationStatus.SKIPPED,
    ):
        raise AppError(ErrorCode.INVALID_STAGE)

    # Serialize all awards to this student to prevent lost increments.
    student = db.scalar(select(User).where(User.id == student_id).with_for_update())
    if student is None:
        raise AppError(ErrorCode.NOT_FOUND)

    stats = db.get(UserLanguageStats, (student_id, game.target_language))
    previous_total = stats.total_points if stats is not None else 0
    previous_level = _level_for_points(previous_total)
    awarded = 0

    if location.status == LocationStatus.COMPLETED:
        existing_award = db.scalar(
            select(PointsLedger.id).where(
                PointsLedger.user_id == student_id,
                PointsLedger.source_type == "location",
                PointsLedger.source_ref == str(session_location_id),
            )
        )
        if existing_award is None:
            db.add(
                PointsLedger(
                    user_id=student_id,
                    source_type="location",
                    source_ref=str(session_location_id),
                    class_id=game.class_id,
                    language=game.target_language,
                    points=100,
                )
            )
            if stats is None:
                stats = UserLanguageStats(
                    user_id=student_id,
                    language=game.target_language,
                    total_points=100,
                )
                db.add(stats)
            else:
                stats.total_points += 100

            awarded = 100
            db.flush()

    total_points = stats.total_points if stats is not None else 0
    level = _level_for_points(total_points)
    return EngagementResult(
        location_points_awarded=awarded,
        total_points=total_points,
        level=level,
        level_up=level > previous_level,
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


def get_active_class_ids(student_id: UUID, db: Session) -> list[UUID]:
    """Return the classes the student is actively enrolled in, most recently joined first.

    An archived class has no active enrolments, so it is never listed. A class the student left
    and rejoined keeps its first `joined_at`, so it can rank lower than "most recent" suggests.
    """
    statement = (
        select(ClassEnrollment.class_id)
        .where(ClassEnrollment.student_id == student_id, ClassEnrollment.is_active.is_(True))
        .order_by(ClassEnrollment.joined_at.desc(), ClassEnrollment.class_id)
    )
    return list(db.scalars(statement))


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


def require_active_enrolment(student_id: UUID, class_id: UUID, db: Session) -> Language:
    """Return the class language if the student is actively enrolled in a class that isn't archived.

    Raises 404 NOT_FOUND, the same for a class that doesn't exist and one the student isn't in, so
    a student can't find out which classes exist. Raises 409 CLASS_ARCHIVED, but only for a student
    whose enrolment ended when the class was archived. Starting a game and selecting a class both
    use it.
    """
    language = get_class_language(class_id, db)
    not_found = AppError(ErrorCode.NOT_FOUND, "Class not found")
    if language is None:
        raise not_found
    if is_class_archived(class_id, db):
        if enrolment_ended_by_archive(student_id, class_id, db):
            raise AppError(ErrorCode.CLASS_ARCHIVED)
        raise not_found
    if not is_actively_enrolled(student_id, class_id, db):
        raise not_found
    return language


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

    user = lock_user(student_id, db)

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


def leave_class(student_id: UUID, class_id: UUID, db: Session) -> None:
    """Leave an active classroom without committing the transaction.

    Preserve the enrolment record so the student can rejoin later.
    If the current class is being left, select another active class
    or clear the current selection.
    """
    student = lock_user(student_id, db)

    if student is None:
        raise AppError(ErrorCode.NOT_FOUND)

    if not student.is_active:
        raise AppError(ErrorCode.ACCOUNT_DISABLED)

    if student.role != UserRole.STUDENT:
        raise AppError(ErrorCode.FORBIDDEN_ROLE)

    enrollment = db.scalar(
        select(ClassEnrollment).where(
            ClassEnrollment.student_id == student_id,
            ClassEnrollment.class_id == class_id,
            ClassEnrollment.is_active.is_(True),
        )
    )

    if enrollment is None:
        raise AppError(ErrorCode.NOT_FOUND)

    enrollment.is_active = False
    enrollment.end_reason = EnrollmentEndReason.LEFT
    enrollment.left_at = datetime.now(UTC)

    if student.current_class_id == class_id:
        student.current_class_id = db.scalar(
            select(ClassEnrollment.class_id)
            .join(Class, Class.id == ClassEnrollment.class_id)
            .where(
                ClassEnrollment.student_id == student_id,
                ClassEnrollment.class_id != class_id,
                ClassEnrollment.is_active.is_(True),
                Class.archived_at.is_(None),
            )
            .order_by(ClassEnrollment.joined_at.desc(), ClassEnrollment.class_id)
            .limit(1)
        )

    db.flush()
