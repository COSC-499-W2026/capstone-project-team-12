"""Global daily streak tracking using the configured local calendar.

The caller must lock the student before invoking these functions.
The caller owns the database transaction.
"""

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.engagement import PointsLedger, UserLanguageStats, UserStats
from app.models.enums import Language, PointsSourceType

STREAK_MILESTONES = {
    7: 100,
    30: 300,
}


def app_calendar_date(now: datetime | None = None) -> date:
    """Return the calendar date in the configured app timezone."""
    if now is None:
        now = datetime.now(UTC)

    if now.tzinfo is None:
        raise ValueError("A timezone-aware datetime is required")

    return now.astimezone(ZoneInfo(get_settings().app_timezone)).date()


def visible_streak(stats: UserStats | None, *, today: date | None = None) -> int:
    """Return the streak visible today without changing its stored history."""
    local_day = today or app_calendar_date()
    if stats is None or stats.last_active_date is None:
        return 0
    return (
        stats.current_streak_days if stats.last_active_date >= local_day - timedelta(days=1) else 0
    )


def challenge_expiry(challenge_date: date) -> datetime:
    """Return the UTC instant when this local challenge day ends."""
    zone = ZoneInfo(get_settings().app_timezone)
    return datetime.combine(challenge_date + timedelta(days=1), time.min, zone).astimezone(UTC)


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
    today = app_calendar_date(now)
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


def award_streak_milestones(
    student_id: UUID,
    streak_days: int,
    *,
    language: Language,
    class_id: UUID | None,
    db: Session,
) -> int:
    """Award any newly reached streak milestones.

    Each milestone is awarded once per student, globally across languages.
    The language and class are taken from the location that triggered it.

    The caller must hold the student lock and own the transaction.
    Returns the total number of newly awarded bonus points.
    """
    total_awarded = 0

    for milestone, reward in STREAK_MILESTONES.items():
        if streak_days < milestone:
            continue

        source_ref = str(milestone)

        existing_award = db.scalar(
            select(PointsLedger.id).where(
                PointsLedger.user_id == student_id,
                PointsLedger.source_type == PointsSourceType.STREAK_MILESTONE,
                PointsLedger.source_ref == source_ref,
            )
        )

        if existing_award is not None:
            continue

        db.add(
            PointsLedger(
                user_id=student_id,
                source_type=PointsSourceType.STREAK_MILESTONE,
                source_ref=source_ref,
                class_id=class_id,
                language=language,
                points=reward,
            )
        )

        stats = db.get(UserLanguageStats, (student_id, language))

        if stats is None:
            stats = UserLanguageStats(
                user_id=student_id,
                language=language,
                total_points=reward,
            )
            db.add(stats)
        else:
            stats.total_points += reward

        total_awarded += reward

        # Persist each unique ledger entry before continuing.
        db.flush()

    return total_awarded
