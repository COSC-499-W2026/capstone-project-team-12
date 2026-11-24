"""Read-only student statistics and classroom analytics."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Date, cast, extract, func, or_, select, tuple_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.engagement.streaks import app_calendar_date, visible_streak
from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.engagement import ClassEnrollment, PointsLedger, UserAchievement, UserStats
from app.models.enums import (
    ArrivalOverride,
    Difficulty,
    DifficultySuggestion,
    Language,
    LocationStatus,
)
from app.models.game import GameSession, SessionLocation


@dataclass(frozen=True)
class AnalyticsTotals:
    """Aggregated outcomes across a class or location."""

    finished: int
    completed: int
    failed: int
    success_rate: float
    photo_attempts: int
    average_time_seconds: float | None
    average_clue_plays: float | None
    flagged_results: int
    gps_overrides: int


@dataclass(frozen=True)
class LocationAnalytics(AnalyticsTotals):
    location_id: UUID


@dataclass(frozen=True)
class ClassAnalytics:
    class_stats: AnalyticsTotals
    locations: list[LocationAnalytics]


def _average_completed_time() -> ColumnElement[float | None]:
    """Average timed completions, capping each location at 30 minutes."""
    location = SessionLocation
    elapsed = extract("epoch", location.completed_at - location.started_at)
    return func.avg(func.least(func.greatest(elapsed, 0), 1800)).filter(
        location.status == LocationStatus.COMPLETED,
        location.started_at.is_not(None),
        location.completed_at.is_not(None),
    )


@dataclass(frozen=True)
class PersonalStatistics:
    language: Language
    difficulty: Difficulty
    locations_completed: int
    completion_rate: float
    average_time_seconds: float | None


def get_personal_statistics(player_id: UUID, db: Session) -> list[PersonalStatistics]:
    """Summarize the player's completed and failed location results by game mode."""
    rows = db.execute(
        select(
            GameSession.target_language,
            GameSession.difficulty,
            func.count(),
            func.count().filter(SessionLocation.status == LocationStatus.COMPLETED),
            _average_completed_time(),
        )
        .join(SessionLocation, SessionLocation.session_id == GameSession.id)
        .where(
            GameSession.student_id == player_id,
            SessionLocation.status.in_((LocationStatus.COMPLETED, LocationStatus.FAILED)),
        )
        .group_by(GameSession.target_language, GameSession.difficulty)
        .order_by(GameSession.target_language, GameSession.difficulty)
    )
    return [
        PersonalStatistics(
            language=Language(language),
            difficulty=Difficulty(difficulty),
            locations_completed=completed,
            completion_rate=round(100 * completed / finished, 2),
            average_time_seconds=round(float(seconds), 2) if seconds is not None else None,
        )
        for language, difficulty, finished, completed, seconds in rows
    ]


def get_class_location_analytics(class_id: UUID, db: Session) -> ClassAnalytics:
    """Aggregate finished classroom locations; completed times are capped at 30 minutes."""
    location = SessionLocation
    completed_filter = location.status == LocationStatus.COMPLETED
    rows = db.execute(
        select(
            location.location_id,
            func.count(),
            func.count().filter(completed_filter),
            func.coalesce(func.sum(location.attempts_used), 0),
            _average_completed_time(),
            func.avg(location.clue_plays),
            func.count().filter(func.jsonb_array_length(location.flags) > 0),
            func.count().filter(location.arrival_override == ArrivalOverride.GPS_UNAVAILABLE),
        )
        .join(GameSession, GameSession.id == location.session_id)
        .where(
            GameSession.class_id == class_id,
            location.status.in_((LocationStatus.COMPLETED, LocationStatus.FAILED)),
        )
        .group_by(func.rollup(location.location_id))
        .order_by(func.grouping(location.location_id).desc(), location.location_id)
    ).all()

    # ROLLUP includes a class summary even when no location results match.
    _, finished, completed, attempts, seconds, clue_plays, flagged, overrides = rows[0]
    class_stats = AnalyticsTotals(
        finished,
        completed,
        finished - completed,
        round(completed / finished * 100, 2) if finished else 0.0,
        attempts,
        round(float(seconds), 2) if seconds is not None else None,
        round(float(clue_plays), 2) if clue_plays is not None else None,
        flagged,
        overrides,
    )
    locations = [
        LocationAnalytics(
            finished,
            completed,
            finished - completed,
            round(completed / finished * 100, 2),
            attempts,
            round(float(seconds), 2) if seconds is not None else None,
            round(float(clue_plays), 2) if clue_plays is not None else None,
            flagged,
            overrides,
            location_id,
        )
        for (
            location_id,
            finished,
            completed,
            attempts,
            seconds,
            clue_plays,
            flagged,
            overrides,
        ) in rows[1:]
    ]
    return ClassAnalytics(class_stats, locations)


@dataclass(frozen=True)
class DifficultyHint:
    """Performance-based difficulty hint for one student and level."""

    difficulty: Difficulty
    finished: int
    completed: int
    failed: int
    success_rate: float | None
    suggestion: DifficultySuggestion | None


def _difficulty_hint(difficulty: Difficulty, completed: int, failed: int) -> DifficultyHint:
    finished = completed + failed
    rate = completed / finished * 100 if finished else None
    suggestion = None
    if finished >= 5 and rate is not None:
        if rate < 40 and difficulty != Difficulty.BASIC:
            suggestion = DifficultySuggestion.EASIER
        elif rate > 85 and difficulty != Difficulty.CHALLENGING:
            suggestion = DifficultySuggestion.HARDER
    return DifficultyHint(
        difficulty,
        finished,
        completed,
        failed,
        round(rate, 2) if rate is not None else None,
        suggestion,
    )


@dataclass(frozen=True)
class StudentAnalytics:
    """Classroom-specific progress for one enrolled student."""

    student_id: UUID
    username: str
    is_active_enrollment: bool
    joined_at: datetime
    left_at: datetime | None
    sessions: int
    completed_locations: int
    failed_locations: int
    classroom_points: int
    total_clue_plays: int
    success_rate: float
    last_activity_at: datetime | None
    streak_days: int
    flags: list[str]
    difficulty_hints: list[DifficultyHint]


def get_class_student_analytics(
    class_id: UUID,
    db: Session,
) -> list[StudentAnalytics]:
    """Return student progress for one classroom, excluding Practice sessions."""
    enrollments = db.execute(
        select(ClassEnrollment, User, UserStats)
        .join(User, User.id == ClassEnrollment.student_id)
        .outerjoin(UserStats, UserStats.user_id == User.id)
        .where(ClassEnrollment.class_id == class_id)
        .order_by(User.username, User.id)
    ).all()

    points = dict(
        db.execute(
            select(PointsLedger.user_id, func.sum(PointsLedger.points))
            .where(PointsLedger.class_id == class_id)
            .group_by(PointsLedger.user_id)
        )
        .tuples()
        .all()
    )

    games = {
        student_id: (count, last_activity)
        for student_id, count, last_activity in db.execute(
            select(GameSession.student_id, func.count(), func.max(GameSession.last_activity_at))
            .where(GameSession.class_id == class_id)
            .group_by(GameSession.student_id)
        )
    }

    location = SessionLocation
    classroom_locations = (
        select(GameSession.student_id)
        .join(location, GameSession.id == location.session_id)
        .where(GameSession.class_id == class_id)
    )
    outcomes = {
        student_id: (completed, failed, clue_plays)
        for student_id, completed, failed, clue_plays in db.execute(
            classroom_locations.add_columns(
                func.count().filter(location.status == LocationStatus.COMPLETED),
                func.count().filter(location.status == LocationStatus.FAILED),
                func.sum(location.clue_plays),
            ).group_by(GameSession.student_id)
        )
    }
    flags: dict[UUID, set[str]] = {}
    for student_id, location_flags in db.execute(
        classroom_locations.add_columns(location.flags).where(
            location.status.in_((LocationStatus.COMPLETED, LocationStatus.FAILED)),
            func.jsonb_array_length(location.flags) > 0,
        )
    ):
        flags.setdefault(student_id, set()).update(location_flags)

    # Aggregate all finished classroom results in one query for every student.
    difficulty_counts = {
        (student_id, difficulty): (completed, failed)
        for student_id, difficulty, completed, failed in db.execute(
            select(
                GameSession.student_id,
                GameSession.difficulty,
                func.count().filter(SessionLocation.status == LocationStatus.COMPLETED),
                func.count().filter(SessionLocation.status == LocationStatus.FAILED),
            )
            .join(SessionLocation, SessionLocation.session_id == GameSession.id)
            .where(
                GameSession.class_id == class_id,
                SessionLocation.status.in_((LocationStatus.COMPLETED, LocationStatus.FAILED)),
            )
            .group_by(GameSession.student_id, GameSession.difficulty)
        )
    }

    today = app_calendar_date()
    results: list[StudentAnalytics] = []
    for enrollment, student, stats in enrollments:
        sessions, last_activity = games.get(student.id, (0, None))
        completed, failed, clue_plays = outcomes.get(student.id, (0, 0, 0))
        finished = completed + failed
        success_rate = round(completed / finished * 100, 2) if finished else 0.0

        results.append(
            StudentAnalytics(
                student_id=student.id,
                username=student.username,
                is_active_enrollment=enrollment.is_active,
                joined_at=enrollment.joined_at,
                left_at=enrollment.left_at,
                sessions=sessions,
                completed_locations=completed,
                failed_locations=failed,
                classroom_points=points.get(student.id, 0),
                total_clue_plays=clue_plays,
                success_rate=success_rate,
                last_activity_at=last_activity,
                streak_days=visible_streak(stats, today=today),
                flags=sorted(flags.get(student.id, set())),
                difficulty_hints=[
                    _difficulty_hint(
                        difficulty, *difficulty_counts.get((student.id, difficulty), (0, 0))
                    )
                    for difficulty in Difficulty
                ],
            )
        )

    return results


def _class_report_period(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    """Return the half-open interval for inclusive report dates in app time."""
    if start_date > end_date:
        raise AppError(ErrorCode.VALIDATION_ERROR, "start_date must not be after end_date")

    timezone = ZoneInfo(get_settings().app_timezone)
    return (
        datetime.combine(start_date, datetime.min.time(), tzinfo=timezone),
        datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone),
    )


@dataclass(frozen=True)
class ClassCluePlay:
    student_id: UUID
    username: str
    game_id: UUID
    game_started_at: datetime
    location_id: UUID
    clue_plays: int
    result: LocationStatus


def get_class_clue_plays(
    class_id: UUID, db: Session, start_date: date, end_date: date, offset: int, page_size: int
) -> tuple[list[ClassCluePlay], int]:
    """Read a page of classroom location records and the total matching the date range."""
    period_start, period_end = _class_report_period(start_date, end_date)
    query = (
        select(
            GameSession.student_id,
            User.username,
            GameSession.id,
            GameSession.started_at,
            SessionLocation.location_id,
            SessionLocation.clue_plays,
            SessionLocation.status,
        )
        .select_from(GameSession)
        .join(User, User.id == GameSession.student_id)
        .join(SessionLocation, SessionLocation.session_id == GameSession.id)
        .where(
            GameSession.class_id == class_id,
            GameSession.started_at >= period_start,
            GameSession.started_at < period_end,
        )
    )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(
        query.order_by(
            GameSession.started_at.desc(),
            GameSession.id,
            SessionLocation.position,
            SessionLocation.id,
        )
        .offset(offset)
        .limit(page_size)
    )
    return [
        ClassCluePlay(
            student_id,
            username,
            game_id,
            started_at,
            location_id,
            clue_plays,
            LocationStatus(status),
        )
        for (
            student_id,
            username,
            game_id,
            started_at,
            location_id,
            clue_plays,
            status,
        ) in rows
    ], total or 0


@dataclass(frozen=True)
class StudentParticipation:
    """Participation grade for one enrolled student in a selected period."""

    student_id: UUID
    username: str
    is_active_enrollment: bool
    enrollment_status: str
    distinct_completed_locations: int
    target_locations: int
    participation_percent: int
    sessions: int
    active_days: int
    points: int
    badges: int


def get_class_participation(
    class_id: UUID,
    db: Session,
    start_date: date,
    end_date: date,
    target_locations: int = 10,
) -> list[StudentParticipation]:
    """Calculate classroom participation using unique (location, difficulty) pairs.

    Dates are inclusive in the configured app timezone. Only completed class-game
    locations count; repeats of the same location at the same difficulty
    are counted once. Former enrollees remain in the results.

    The service is read-only and does not commit or flush.
    """
    if target_locations < 1:
        raise AppError(ErrorCode.VALIDATION_ERROR, "target_locations must be positive")

    period_start, period_end = _class_report_period(start_date, end_date)

    enrollments = db.execute(
        select(ClassEnrollment, User)
        .join(User, User.id == ClassEnrollment.student_id)
        .where(ClassEnrollment.class_id == class_id)
        .order_by(User.username, User.id)
    ).all()

    pair = tuple_(SessionLocation.location_id, GameSession.difficulty)
    active_date = cast(
        func.timezone(get_settings().app_timezone, SessionLocation.completed_at), Date
    )
    completions = {
        student_id: (count, active_days)
        for student_id, count, active_days in db.execute(
            select(
                GameSession.student_id,
                func.count(pair.distinct()),
                func.count(active_date.distinct()),
            )
            .join(SessionLocation, SessionLocation.session_id == GameSession.id)
            .where(
                GameSession.class_id == class_id,
                SessionLocation.status == LocationStatus.COMPLETED,
                SessionLocation.completed_at >= period_start,
                SessionLocation.completed_at < period_end,
            )
            .group_by(GameSession.student_id)
        )
    }
    sessions = dict(
        db.execute(
            select(GameSession.student_id, func.count())
            .where(
                GameSession.class_id == class_id,
                GameSession.started_at >= period_start,
                GameSession.started_at < period_end,
            )
            .group_by(GameSession.student_id)
        )
        .tuples()
        .all()
    )
    ledger = select(PointsLedger.user_id).where(
        PointsLedger.class_id == class_id,
        PointsLedger.created_at >= period_start,
        PointsLedger.created_at < period_end,
    )
    points: dict[UUID, int] = dict(
        db.execute(ledger.add_columns(func.sum(PointsLedger.points)).group_by(PointsLedger.user_id))
        .tuples()
        .all()
    )
    # Badge unlocks and their scoring ledger entries share the transaction timestamp.
    badges = dict(
        db.execute(
            select(UserAchievement.user_id, func.count())
            .where(
                ledger.where(
                    PointsLedger.user_id == UserAchievement.user_id,
                    PointsLedger.created_at == UserAchievement.unlocked_at,
                    or_(
                        UserAchievement.language.is_(None),
                        PointsLedger.language == UserAchievement.language,
                    ),
                ).exists()
            )
            .group_by(UserAchievement.user_id)
        )
        .tuples()
        .all()
    )

    results: list[StudentParticipation] = []
    for enrollment, student in enrollments:
        count, active_days = completions.get(student.id, (0, 0))
        status = "active" if enrollment.is_active else str(enrollment.end_reason)
        results.append(
            StudentParticipation(
                student_id=student.id,
                username=student.username,
                is_active_enrollment=enrollment.is_active,
                enrollment_status=status,
                distinct_completed_locations=count,
                target_locations=target_locations,
                sessions=sessions.get(student.id, 0),
                active_days=active_days,
                points=points.get(student.id, 0),
                badges=badges.get(student.id, 0),
                participation_percent=min(
                    100, (200 * count + target_locations) // (2 * target_locations)
                ),
            )
        )

    return results
