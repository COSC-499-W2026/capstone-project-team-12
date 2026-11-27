"""Public interface of app.engagement: the only module other components may import.

Implemented:
- Issue #11: Class language, active enrolment, and archived-state lookups.
- Issue #106: Access code validation and student enrolment.
- Issue #84: Classroom creation, access code generation, settings update
  services (update_class_settings), classroom listing via list_staff_classes,
  and code deactivation.
- Issue #85: Student leave-class service and current-class reassignment.
- Issue #413: The student's active classes with their names and languages
  (list_active_classes), for GET /me/profile.

Implemented in part:
- Issue #15: Location scoring, per-language levels, and completion badges.
  Streak milestones and advanced achievements are pending.
- Issue #86: Points, levels, and completion badge unlocking.
  Student engagement, achievements, and statistics API endpoints are pending.
- Issue #47 (A23): what one game added (level as of the game, level_up, its badges),
  for the game summary (get_game_engagement).

Callers must check class existence and archived status before enrolment
so archived classes produce CLASS_ARCHIVED rather than an enrolment error.

Engagement and enrolment functions do not commit transactions. Callers are
responsible for committing or rolling back the entire operation.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.engagement.badges import (
    UnlockedAchievement,
    list_unlocked_achievements,
    unlock_completion_badges,
    unlock_streak_badges,
)
from app.engagement.challenge_progress import record_challenge_progress
from app.engagement.class_statistics import (
    LocationAnalytics,
    StudentAnalytics,
    get_class_location_analytics,
    get_class_participation,
    get_class_student_analytics,
    get_personal_statistics,
)
from app.engagement.classes import (
    create_class,
    deactivate_access_code,
    generate_access_code,
    list_class_access_codes,
    list_staff_classes,
    update_class_settings,
)
from app.engagement.daily_challenges import ensure_daily_challenges
from app.engagement.leaderboard import LeaderboardEntry, get_class_leaderboard
from app.engagement.stats import (
    GameEngagement,
    LanguageEngagementStats,
    get_game_engagement,
    get_language_engagement_stats,
)
from app.engagement.stats import (
    level_for_points as _level_for_points,
)
from app.engagement.streaks import (
    app_calendar_date,
    award_streak_milestones,
    challenge_expiry,
    record_active_day,
    visible_streak,
)
from app.errors import AppError, ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import (
    AccessCode,
    Class,
    ClassEnrollment,
    DailyChallenge,
    PointsLedger,
    UserLanguageStats,
    UserStats,
)
from app.models.enums import (
    ClassStaffRole,
    EnrollmentEndReason,
    Language,
    PointsSourceType,
    UserRole,
)
from app.models.game import GameSession, SessionLocation
from app.services.locks import lock_user

LOCATION_COMPLETION_POINTS = 100

__all__ = [
    "app_calendar_date",
    "challenge_expiry",
    "ensure_daily_challenges",
    "get_and_report_streak",
    "get_stored_streak",
    "list_daily_challenges",
    "visible_streak",
    "EngagementResult",
    "apply_location_result",
    "create_class",
    "deactivate_access_code",
    "enrolment_ended_by_archive",
    "generate_access_code",
    "get_active_class_ids",
    "ActiveClass",
    "list_active_classes",
    "get_class_language",
    "is_actively_enrolled",
    "is_class_archived",
    "lock_open_class",
    "leave_class",
    "list_class_access_codes",
    "remove_student",
    "restore_student",
    "archive_class",
    "list_staff_classes",
    "require_active_enrolment",
    "update_class_settings",
    "JoinedClass",
    "validate_code_and_enrol",
    "LanguageEngagementStats",
    "get_language_engagement_stats",
    "GameEngagement",
    "get_game_engagement",
    "UnlockedAchievement",
    "list_unlocked_achievements",
    "LeaderboardEntry",
    "get_class_leaderboard",
    "LocationAnalytics",
    "get_class_location_analytics",
    "get_class_participation",
    "get_personal_statistics",
    "StudentAnalytics",
    "get_class_student_analytics",
]


def get_and_report_streak(student_id: UUID, db: Session, *, today: date) -> tuple[int, int, bool]:
    """Return the visible and best streaks and report an expiration once."""
    if lock_user(student_id, db) is None:
        raise AppError(ErrorCode.NOT_FOUND)

    stats = db.get(UserStats, student_id)
    if stats is None:
        return 0, 0, False

    current = visible_streak(stats, today=today)
    broken = (
        current == 0
        and stats.current_streak_days > 0
        and stats.last_active_date != stats.reported_break_date
    )
    if broken:
        stats.reported_break_date = stats.last_active_date
    return current, stats.best_streak_days, broken


def get_stored_streak(student_id: UUID, db: Session) -> UserStats | None:
    """The student's streak as last stored, or None before their first completed location.

    For the export of their account: unlike get_and_report_streak, it changes nothing.
    """
    return db.get(UserStats, student_id)


def list_daily_challenges(student_id: UUID, db: Session) -> list[DailyChallenge]:
    """Every daily challenge the student was given, oldest day first, with its progress.

    For the export of their account: unlike ensure_daily_challenges, it never creates any.
    """
    challenges = select(DailyChallenge).where(DailyChallenge.user_id == student_id)
    return list(db.scalars(challenges.order_by(DailyChallenge.challenge_date, DailyChallenge.slot)))


@dataclass(frozen=True)
class EngagementResult:
    """Engagement information returned after processing a location result.

    Defines the engagement response contract for Issue #15.
    Completion badges, daily streaks, and daily challenge progress are implemented.
    Streak milestones and advanced achievements are pending implementation.

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
    *,
    completed: bool,
    target_language: Language,
    class_id: UUID | None,
    db: Session,
) -> EngagementResult:
    """Process a finished session location and return engagement information.

    Interface contract for Issue #15. Implements location scoring, points
    persistence, per-language totals, and level calculation.

    Ownership:
        student_id identifies the authenticated student owning the session.
        The game caller must verify that the session location exists, belongs
        to the student's game session, and has reached a final status before
        invoking this function.

    Preconditions:
    The game caller must acquire the student lock using
    lock_user(student_id, db) before locking the game session
    or session location. This consistent lock order prevents
    deadlocks with game start and class/language switching.

    The caller must validate location existence and ownership,
    set its final status, and invoke this function within the
    same transaction. Missing locations must raise NOT_FOUND.

    Location scoring:
        - A completed location awards 100 points when first processed.
        - Failed and skipped locations award zero points.
        - Failed and skipped locations return existing cumulative language
          points and derived level, with level_up=False.
        - Failed and skipped locations do not count as active streak days.
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

        Otherwise, the current streak starts at one.
        The best streak is the maximum current streak reached.

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
        Achievements award zero points.

        Eligibility is checked after processing a completed location
        and any resulting streak change.

    Validation:
        The game caller must verify that:
        - The session location exists.
        - Its associated game session belongs to student_id.
        - The location has reached a valid final status.
        - The supplied language and class match the game session.

        Validation failures must not modify engagement state.

        Engagement verifies that the student exists before updating
        engagement records.

    Transactions:
        The caller owns the database transaction.

        Engagement may add records and flush pending database changes
        to enforce constraints or obtain generated identifiers.

        Engagement must not commit or roll back the transaction.

        Location, session, ledger, and engagement-stat changes must
        remain consistent within the caller's transaction.

    Pending functionality:
        Streak milestones are awarded once at 7 days (100 points) and
        30 days (300 points). Advanced achievement types are pending.
        Completion badges can be unlocked by successful locations.

    Args:
        session_location_id: UUID of the finished session location.
        student_id: UUID of the authenticated student owning the session.
        completed: Whether the location finished successfully.
        target_language: Language of the validated game session.
        class_id: Class UUID, or None for Practice sessions.
        db: SQLAlchemy session belonging to the caller's transaction.

    Returns:
        EngagementResult containing newly awarded location points and
        cumulative engagement information.

    Raises:
        AppError(ErrorCode.NOT_FOUND): Student does not exist.
    """
    # Serialize awards for this student to prevent lost increments.
    student = lock_user(student_id, db)
    if student is None:
        raise AppError(ErrorCode.NOT_FOUND)

    stats = db.get(UserLanguageStats, (student_id, target_language))
    previous_total = stats.total_points if stats is not None else 0
    previous_level = _level_for_points(previous_total)
    awarded = 0

    if completed:
        existing_award = db.scalar(
            select(PointsLedger.id).where(
                PointsLedger.user_id == student_id,
                PointsLedger.source_type == PointsSourceType.LOCATION,
                PointsLedger.source_ref == str(session_location_id),
            )
        )

        if existing_award is None:
            db.add(
                PointsLedger(
                    user_id=student_id,
                    source_type=PointsSourceType.LOCATION,
                    source_ref=str(session_location_id),
                    class_id=class_id,
                    language=target_language,
                    points=LOCATION_COMPLETION_POINTS,
                )
            )

            if stats is None:
                stats = UserLanguageStats(
                    user_id=student_id,
                    language=target_language,
                    total_points=LOCATION_COMPLETION_POINTS,
                )
                db.add(stats)
            else:
                stats.total_points += LOCATION_COMPLETION_POINTS

            awarded = LOCATION_COMPLETION_POINTS
            db.flush()

    challenges_completed: tuple[str, ...] = ()
    achievements_unlocked: tuple[str, ...] = ()
    if awarded > 0:
        # Reuse the finalized location's game for challenge progress and session badges.
        game = db.execute(
            select(GameSession.id, GameSession.difficulty)
            .join(SessionLocation, SessionLocation.session_id == GameSession.id)
            .where(
                SessionLocation.id == session_location_id,
                GameSession.student_id == student_id,
            )
        ).one_or_none()
        if game is None:
            raise AppError(ErrorCode.NOT_FOUND)
        challenges_completed = record_challenge_progress(
            student_id,
            difficulty=game.difficulty,
            language=target_language,
            class_id=class_id,
            db=db,
        )
        achievements_unlocked = unlock_completion_badges(
            student_id, target_language, db, session_id=game.id
        )
        streak_days = record_active_day(student_id, db)
        award_streak_milestones(
            student_id,
            streak_days,
            language=target_language,
            class_id=class_id,
            db=db,
        )
        achievements_unlocked = tuple(
            sorted((*achievements_unlocked, *unlock_streak_badges(student_id, streak_days, db)))
        )
    else:
        streak_days = visible_streak(db.get(UserStats, student_id))

    total_points = stats.total_points if stats is not None else 0
    level = _level_for_points(total_points)

    return EngagementResult(
        location_points_awarded=awarded,
        total_points=total_points,
        level=level,
        level_up=level > previous_level,
        streak_days=streak_days,
        challenges_completed=challenges_completed,
        achievements_unlocked=achievements_unlocked,
    )


def get_class_language(class_id: UUID, db: Session) -> Language | None:
    """Return the class language, or None if the class does not exist."""
    school_class = db.get(Class, class_id)
    if school_class is None:
        return None
    return Language(school_class.target_language)


@dataclass(frozen=True)
class ActiveClass:
    """A class the student is actively enrolled in, for the student's class switcher.

    `language` is the class's target language; `joined_at` is the first join date, kept on rejoin.
    """

    id: UUID
    name: str
    language: Language
    joined_at: datetime


def list_active_classes(student_id: UUID, db: Session) -> list[ActiveClass]:
    """Return the classes the student is actively enrolled in, most recently joined first.

    Each with its name, language and the student's first join date. get_active_class_ids lists
    their ids from this same query.
    """
    statement = (
        select(Class.id, Class.name, Class.target_language, ClassEnrollment.joined_at)
        .join(Class, Class.id == ClassEnrollment.class_id)
        .where(ClassEnrollment.student_id == student_id, ClassEnrollment.is_active.is_(True))
        .order_by(ClassEnrollment.joined_at.desc(), ClassEnrollment.class_id)
    )
    return [
        ActiveClass(
            id=row.id,
            name=row.name,
            language=Language(row.target_language),
            joined_at=row.joined_at,
        )
        for row in db.execute(statement)
    ]


def get_active_class_ids(student_id: UUID, db: Session) -> list[UUID]:
    """Return the classes the student is actively enrolled in, most recently joined first.

    An archived class has no active enrolments, so it is never listed. A class the student left
    and rejoined keeps its first `joined_at`, so it can rank lower than "most recent" suggests.
    """
    return [c.id for c in list_active_classes(student_id, db)]


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


def lock_open_class(class_id: UUID, db: Session) -> str:
    """Lock and reload a class that isn't archived, and return its name. Lock any user first,
    as joining and archiving do, so an archive that commits meanwhile is seen.

    Raises 404 NOT_FOUND for an unknown class and 409 CLASS_ARCHIVED for an archived one.
    """
    if db.get(Class, class_id) is None:
        raise AppError(ErrorCode.NOT_FOUND)
    school_class = _lock_class(class_id, db)
    if school_class.archived_at is not None:
        raise AppError(ErrorCode.CLASS_ARCHIVED)
    return school_class.name


def _lock_class(class_id: UUID, db: Session) -> Class:
    """Lock and refresh a classroom whose existence has been established."""
    return db.execute(
        select(Class)
        .where(Class.id == class_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    ).scalar_one()


def _reject_assistant(student_id: UUID, class_id: UUID, db: Session) -> None:
    assistant = db.scalar(
        select(ClassStaff.user_id).where(
            ClassStaff.user_id == student_id,
            ClassStaff.class_id == class_id,
            ClassStaff.role == ClassStaffRole.ASSISTANT,
        )
    )
    if assistant is not None:
        raise AppError(ErrorCode.TA_SAME_CLASS_CONFLICT)


def _active_enrolment_for_language(
    student_id: UUID, language: str, db: Session
) -> ClassEnrollment | None:
    return db.scalar(
        select(ClassEnrollment).where(
            ClassEnrollment.student_id == student_id,
            ClassEnrollment.language == language,
            ClassEnrollment.is_active.is_(True),
        )
    )


@dataclass(frozen=True)
class JoinedClass:
    class_id: UUID
    language: Language


def validate_code_and_enrol(
    student_id: UUID,
    code: str,
    db: Session,
) -> JoinedClass:
    """Validate an access code and enrol a student in its class.

    Return the class ID and language, selecting the class for a new or reactivated enrolment.

    An active enrolment can be retried with any code for its class, including
    expired, exhausted, or deactivated codes, without changing the enrolment,
    current class selection, or code usage.

    Raises:
        INVALID_ACCESS_CODE: Code is unknown, inactive, expired, or used up
            unless it qualifies for an existing-enrolment retry.
        ACCOUNT_DISABLED: User account is deactivated.
        FORBIDDEN_ROLE: User is missing or is not a student.
        CLASS_ARCHIVED: Class is archived.
        TA_SAME_CLASS_CONFLICT: Student assists the target class.
        ALREADY_IN_CLASS_FOR_LANGUAGE: Student already has an active class
            in the same language in a different classroom.
        REMOVED_FROM_CLASS: Student was removed from this class.

    Input is normalized by removing spaces and hyphens and converting
    to uppercase.

    Locks are acquired in student, access code, then class order.
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

    if access_code is None:
        raise AppError(ErrorCode.INVALID_ACCESS_CODE)

    existing = db.scalar(
        select(ClassEnrollment).where(
            ClassEnrollment.student_id == student_id,
            ClassEnrollment.class_id == access_code.class_id,
        )
    )

    is_retry = existing is not None and existing.is_active
    # Unavailable codes may only be used to retry an active enrolment in this class.
    if not is_retry and (
        not access_code.is_active
        or access_code.expires_at <= now
        or (access_code.max_uses is not None and access_code.uses_count >= access_code.max_uses)
    ):
        raise AppError(ErrorCode.INVALID_ACCESS_CODE)

    school_class = _lock_class(access_code.class_id, db)
    if school_class.archived_at is not None:
        raise AppError(ErrorCode.CLASS_ARCHIVED)

    _reject_assistant(student_id, school_class.id, db)

    joined = JoinedClass(school_class.id, Language(school_class.target_language))
    if is_retry:
        return joined

    if existing is not None and existing.end_reason == EnrollmentEndReason.REMOVED:
        raise AppError(ErrorCode.REMOVED_FROM_CLASS)

    active_enrolment = _active_enrolment_for_language(student_id, school_class.target_language, db)
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

    user.current_class_id = school_class.id

    # Increment atomically without committing the caller's transaction.
    db.execute(
        update(AccessCode)
        .where(AccessCode.id == access_code.id)
        .values(uses_count=AccessCode.uses_count + 1)
    )

    db.flush()
    return joined


def _next_current_class(student_id: UUID, class_id: UUID, db: Session) -> UUID | None:
    return db.scalar(
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

    _end_enrolment(student, enrollment, EnrollmentEndReason.LEFT, db)


def _end_enrolment(
    student: User, enrollment: ClassEnrollment, reason: EnrollmentEndReason, db: Session
) -> None:
    class_id = enrollment.class_id
    enrollment.is_active = False
    enrollment.end_reason = reason
    enrollment.left_at = datetime.now(UTC)

    if student.current_class_id == class_id:
        student.current_class_id = _next_current_class(student.id, class_id, db)

    db.flush()


def _lock_class_enrolment(
    student_id: UUID, class_id: UUID, db: Session
) -> tuple[User, ClassEnrollment]:
    student = lock_user(student_id, db)
    if student is None:
        raise AppError(ErrorCode.NOT_FOUND)

    school_class = _lock_class(class_id, db)
    if school_class.archived_at is not None:
        raise AppError(ErrorCode.CLASS_ARCHIVED)

    enrolment = db.scalar(
        select(ClassEnrollment)
        .where(ClassEnrollment.student_id == student_id, ClassEnrollment.class_id == class_id)
        .execution_options(populate_existing=True)
    )
    if enrolment is None:
        raise AppError(ErrorCode.NOT_FOUND)
    return student, enrolment


def remove_student(student_id: UUID, class_id: UUID, db: Session) -> bool:
    """End an active enrolment and return whether it changed. Does not commit."""
    student, enrolment = _lock_class_enrolment(student_id, class_id, db)
    if enrolment.end_reason == EnrollmentEndReason.REMOVED:
        return False
    if not enrolment.is_active:
        raise AppError(ErrorCode.NOT_FOUND)
    _end_enrolment(student, enrolment, EnrollmentEndReason.REMOVED, db)
    return True


def restore_student(student_id: UUID, class_id: UUID, db: Session) -> None:
    """Reactivate a removed enrolment in place. Does not consume a code or commit."""
    _, enrolment = _lock_class_enrolment(student_id, class_id, db)
    if enrolment.is_active:
        return
    if enrolment.end_reason != EnrollmentEndReason.REMOVED:
        raise AppError(ErrorCode.NOT_FOUND)

    _reject_assistant(student_id, class_id, db)
    if _active_enrolment_for_language(student_id, enrolment.language, db) is not None:
        raise AppError(ErrorCode.ALREADY_IN_CLASS_FOR_LANGUAGE)

    enrolment.is_active = True
    enrolment.end_reason = None
    enrolment.left_at = None
    db.flush()


def archive_class(class_id: UUID, db: Session) -> None:
    """Archive a class and end its enrolments without committing.

    Lock enrolled students before the class, matching join and serializing
    against game start, reset, leave and class selection. A join that committed
    while these locks were being acquired requires a fresh pass.
    """
    enrolled_students = select(ClassEnrollment.student_id).where(
        ClassEnrollment.class_id == class_id,
        ClassEnrollment.is_active.is_(True),
    )
    while True:
        locks = db.begin_nested()
        students = db.scalars(
            select(User)
            .where(User.id.in_(enrolled_students) | (User.current_class_id == class_id))
            .order_by(User.id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        ).all()
        school_class = _lock_class(class_id, db)
        if school_class.archived_at is not None:
            locks.commit()
            return

        if set(db.scalars(enrolled_students)).issubset(student.id for student in students):
            locks.commit()
            break
        locks.rollback()

    now = datetime.now(UTC)
    school_class.archived_at = now
    db.execute(
        update(ClassEnrollment)
        .where(
            ClassEnrollment.class_id == class_id,
            ClassEnrollment.is_active.is_(True),
        )
        .values(is_active=False, end_reason=EnrollmentEndReason.ARCHIVED, left_at=now)
    )
    for student in students:
        if student.current_class_id == class_id:
            student.current_class_id = _next_current_class(student.id, class_id, db)
    db.flush()
