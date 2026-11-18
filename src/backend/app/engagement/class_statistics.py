"""Read-only classroom analytics services (A16)."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import (
    ArrivalOverride,
    LocationStatus,
)
from app.models.game import GameSession, SessionLocation


@dataclass(frozen=True)
class LocationAnalytics:
    """Aggregated outcomes for one classroom location."""

    location_id: UUID
    finished: int
    completed: int
    failed: int
    success_rate: float
    photo_attempts: int
    average_time_seconds: float | None
    flagged_results: int
    gps_overrides: int


def get_class_location_analytics(
    class_id: UUID,
    db: Session,
) -> list[LocationAnalytics]:
    """Return per-location statistics for a classroom.

    Only completed and failed locations contribute to success rates.
    Practice games, unfinished locations, and skipped locations are excluded.
    Each completed location's elapsed time is capped at 30 minutes.

    This operation is read-only and does not commit or flush.
    """
    rows = db.scalars(
        select(SessionLocation)
        .join(
            GameSession,
            GameSession.id == SessionLocation.session_id,
        )
        .where(
            GameSession.class_id == class_id,
            SessionLocation.status.in_(
                (
                    LocationStatus.COMPLETED,
                    LocationStatus.FAILED,
                )
            ),
        )
    )

    totals: dict[UUID, dict[str, Any]] = {}

    for location in rows:
        entry = totals.setdefault(
            location.location_id,
            {
                "finished": 0,
                "completed": 0,
                "failed": 0,
                "photo_attempts": 0,
                "times": [],
                "flagged_results": 0,
                "gps_overrides": 0,
            },
        )

        entry["finished"] += 1
        entry["photo_attempts"] += location.attempts_used

        if location.flags:
            entry["flagged_results"] += 1

        if location.arrival_override == ArrivalOverride.GPS_UNAVAILABLE:
            entry["gps_overrides"] += 1

        if location.status == LocationStatus.COMPLETED:
            entry["completed"] += 1

            if location.started_at is not None and location.completed_at is not None:
                elapsed_seconds = max(
                    0.0,
                    (location.completed_at - location.started_at).total_seconds(),
                )

                capped_seconds = min(elapsed_seconds, 1800.0)
                entry["times"].append(capped_seconds)

        elif location.status == LocationStatus.FAILED:
            entry["failed"] += 1

    results: list[LocationAnalytics] = []

    for location_id, entry in sorted(totals.items()):
        times = entry["times"]

        success_rate = round(
            entry["completed"] / entry["finished"] * 100,
            2,
        )

        average_time_seconds = round(sum(times) / len(times), 2) if times else None

        results.append(
            LocationAnalytics(
                location_id=location_id,
                finished=entry["finished"],
                completed=entry["completed"],
                failed=entry["failed"],
                success_rate=success_rate,
                photo_attempts=entry["photo_attempts"],
                average_time_seconds=average_time_seconds,
                flagged_results=entry["flagged_results"],
                gps_overrides=entry["gps_overrides"],
            )
        )

    return results
