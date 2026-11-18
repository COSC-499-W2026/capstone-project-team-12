"""Read-only classroom analytics services (A16)."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session

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
    rows = db.execute(
        select(
            location.location_id,
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
        .order_by(location.location_id.asc().nulls_first())
    ).all()

    # ROLLUP includes a class summary even when no location results match.
    _, finished, completed, attempts, seconds, flagged, overrides = rows[0]
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
        for position, (
            location_id,
            finished,
            completed,
            attempts,
            seconds,
            flagged,
            overrides,
        ) in enumerate(rows[1:], start=1)
    ]
    return ClassAnalytics(class_stats, locations)
