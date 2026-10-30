"""Badge unlocking helpers for completed locations (Issue #86)."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.engagement import Achievement, PointsLedger, UserAchievement
from app.models.enums import PointsSourceType


def unlock_completion_badges(
    student_id: UUID,
    language: str,
    db: Session,
) -> tuple[str, ...]:
    """Unlock eligible completion badges once per student and language.

    Called while apply_location_result holds the student's row lock.
    Does not commit; the caller owns the transaction.
    """
    completed_count = (
        db.scalar(
            select(func.count(PointsLedger.id)).where(
                PointsLedger.user_id == student_id,
                PointsLedger.language == language,
                PointsLedger.source_type == PointsSourceType.LOCATION,
            )
        )
        or 0
    )

    definitions = db.scalars(
        select(Achievement).where(
            Achievement.is_active.is_(True),
        )
    ).all()

    unlocked: list[str] = []

    for achievement in definitions:
        condition = achievement.unlock_condition

        if condition.get("type") != "locations_completed":
            continue

        threshold = condition.get("threshold")
        if type(threshold) is not int or threshold < 1:
            continue

        if completed_count < threshold:
            continue

        existing = db.scalar(
            select(UserAchievement.id).where(
                UserAchievement.user_id == student_id,
                UserAchievement.achievement_id == achievement.id,
                UserAchievement.language == language,
            )
        )
        if existing is not None:
            continue

        db.add(
            UserAchievement(
                user_id=student_id,
                achievement_id=achievement.id,
                language=language,
            )
        )
        unlocked.append(achievement.achievement_key)

    db.flush()
    return tuple(sorted(unlocked))
