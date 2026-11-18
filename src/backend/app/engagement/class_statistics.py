"""Read-only classroom analytics services (A16)."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Date, cast, extract, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.accounts import User
from app.models.engagement import ClassEnrollment, PointsLedger
from app.models.enums import ArrivalOverride, LocationStatus
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
    flagged_results: int
    gps_overrides: int


@dataclass(frozen=True)
class LocationAnalytics(AnalyticsTotals):
    location_id: UUID
    label: str


@dataclass(frozen=True)
class ClassAnalytics:
    class_stats: AnalyticsTotals
    locations: list[LocationAnalytics]


def get_class_location_analytics(class_id: UUID, db: Session) -> ClassAnalytics:
    """Aggregate finished classroom locations; completed times are capped at 30 minutes."""
    location = SessionLocation
    completed_filter = location.status == LocationStatus.COMPLETED
    elapsed = extract("epoch", location.completed_at - location.started_at)
    average = func.avg(func.least(func.greatest(elapsed, 0), 1800)).filter(
        completed_filter, location.started_at.is_not(None), location.completed_at.is_not(None)
    )
    first_position = func.min(location.position)
    rows = db.execute(
        select(
            location.location_id,
            first_position,
            func.count(),
            func.count().filter(completed_filter),
            func.coalesce(func.sum(location.attempts_used), 0),
            average,
            func.count().filter(func.jsonb_array_length(location.flags) > 0),
            func.count().filter(location.arrival_override == ArrivalOverride.GPS_UNAVAILABLE),
        )
        .join(GameSession, GameSession.id == location.session_id)
        .where(
            GameSession.class_id == class_id,
            location.status.in_((LocationStatus.COMPLETED, LocationStatus.FAILED)),
        )
        .group_by(func.rollup(location.location_id))
        .order_by(func.grouping(location.location_id).desc(), first_position, location.location_id)
    ).all()

    # ROLLUP includes a class summary even when no location results match.
    _, _, finished, completed, attempts, seconds, flagged, overrides = rows[0]
    class_stats = AnalyticsTotals(
        finished,
        completed,
        finished - completed,
        round(completed / finished * 100, 2) if finished else 0.0,
        attempts,
        round(float(seconds), 2) if seconds is not None else None,
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
            flagged,
            overrides,
            location_id,
            f"Location {position}",
        )
        for (
            location_id,
            position,
            finished,
            completed,
            attempts,
            seconds,
            flagged,
            overrides,
        ) in rows[1:]
    ]
    return ClassAnalytics(class_stats, locations)


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
    success_rate: float
    last_activity_at: datetime | None
    streak_days: int
    flags: list[str]


def get_class_student_analytics(
    class_id: UUID,
    db: Session,
) -> list[StudentAnalytics]:
    """Return student progress for one classroom, excluding Practice sessions."""
    enrollments = db.execute(
        select(ClassEnrollment, User)
        .join(User, User.id == ClassEnrollment.student_id)
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

    zone = get_settings().app_timezone
    today = datetime.now(UTC).astimezone(ZoneInfo(zone)).date()
    local_day = cast(func.timezone(zone, SessionLocation.completed_at), Date)
    enrolled = select(ClassEnrollment.student_id).where(ClassEnrollment.class_id == class_id)
    active_days = db.execute(
        select(GameSession.student_id, local_day.label("day"))
        .join(SessionLocation, SessionLocation.session_id == GameSession.id)
        .where(
            GameSession.student_id.in_(enrolled),
            SessionLocation.status == LocationStatus.COMPLETED,
            SessionLocation.completed_at.is_not(None),
            local_day <= today,
        )
        .distinct()
        .order_by(GameSession.student_id, local_day.desc())
    )
    streaks: dict[UUID, int] = {}
    latest_days: dict[UUID, date] = {}
    for student_id, day in active_days:
        latest = latest_days.setdefault(student_id, day)
        length = streaks.get(student_id, 0)
        if latest >= today - timedelta(days=1) and day == latest - timedelta(days=length):
            streaks[student_id] = length + 1

    games = db.scalars(select(GameSession).where(GameSession.class_id == class_id)).all()

    games_by_student: dict[UUID, list[GameSession]] = {}
    for game in games:
        games_by_student.setdefault(game.student_id, []).append(game)

    locations = db.execute(
        select(SessionLocation, GameSession.student_id)
        .join(GameSession, GameSession.id == SessionLocation.session_id)
        .where(GameSession.class_id == class_id)
    ).all()

    outcomes: dict[UUID, dict[str, int]] = {}
    flags: dict[UUID, set[str]] = {}
    for location, student_id in locations:
        flags.setdefault(student_id, set()).update(location.flags)
        counts = outcomes.setdefault(student_id, {"completed": 0, "failed": 0})

        if location.status == LocationStatus.COMPLETED:
            counts["completed"] += 1
        elif location.status == LocationStatus.FAILED:
            counts["failed"] += 1

    results: list[StudentAnalytics] = []

    for enrollment, student in enrollments:
        student_games = games_by_student.get(student.id, [])
        counts = outcomes.get(student.id, {"completed": 0, "failed": 0})

        finished = counts["completed"] + counts["failed"]
        success_rate = round(counts["completed"] / finished * 100, 2) if finished else 0.0

        last_activity = max(
            (game.last_activity_at for game in student_games),
            default=None,
        )

        results.append(
            StudentAnalytics(
                student_id=student.id,
                username=student.username,
                is_active_enrollment=enrollment.is_active,
                joined_at=enrollment.joined_at,
                left_at=enrollment.left_at,
                sessions=len(student_games),
                completed_locations=counts["completed"],
                failed_locations=counts["failed"],
                classroom_points=points.get(student.id, 0),
                success_rate=success_rate,
                last_activity_at=last_activity,
                streak_days=streaks.get(student.id, 0),
                flags=sorted(flags.get(student.id, set())),
            )
        )

    return results
