"""Global daily streak tracking using the configured local calendar.

The caller must lock the student before invoking these functions.
The caller owns the database transaction.
"""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.engagement import UserStats


def vancouver_date(now: datetime | None = None) -> date:
    """Return the calendar date in the configured app timezone."""
    if now is None:
        now = datetime.now(UTC)

    if now.tzinfo is None:
        raise ValueError("A timezone-aware datetime is required")

    return now.astimezone(ZoneInfo(get_settings().app_timezone)).date()


def record_active_day(
    student_id: UUID,
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    """Record a completed location and return the student's current streak.

    Multiple completions on one local day count only once.
    Consecutive local days extend the streak; gaps reset it to one.

    The caller must hold the student lock and must commit or roll back.
    """
    today = vancouver_date(now)
    stats = db.get(UserStats, student_id)

    if stats is None:
        stats = UserStats(
            user_id=student_id,
            current_streak_days=1,
            best_streak_days=1,
            last_active_date=today,
        )
        db.add(stats)
        db.flush()
        return 1

    if stats.last_active_date == today:
        return stats.current_streak_days

    if stats.last_active_date is not None and stats.last_active_date > today:
        return stats.current_streak_days

    if stats.last_active_date == today - timedelta(days=1):
        stats.current_streak_days += 1
    else:
        stats.current_streak_days = 1

    stats.best_streak_days = max(
        stats.best_streak_days,
        stats.current_streak_days,
    )
    stats.last_active_date = today

    db.flush()
    return stats.current_streak_days
