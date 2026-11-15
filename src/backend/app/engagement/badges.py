"""Badge unlocking and achievement retrieval helpers (Issue #86)."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.engagement import Achievement, PointsLedger, UserAchievement
from app.models.enums import Language, PointsSourceType


def unlock_completion_badges(
    student_id: UUID,
    language: str,
    db: Session,
) -> tuple[str, ...]:
    """Unlock eligible completion badges once per student and language.

    Called while apply_location_result holds the student's row lock.
    Only invoked for newly awarded location points; newly activated
    achievements are not retroactively unlocked for past completions.
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


@dataclass(frozen=True)
class AchievementProgress:
    """Progress toward a measurable achievement."""

    current: int
    required: int


@dataclass(frozen=True)
class UnlockedAchievement:
    """An achievement definition and the user's unlock state."""

    achievement_key: str
    category: str
    icon_key: str
    language: Language | None
    unlocked: bool
    unlocked_at: datetime | None
    progress: AchievementProgress | None


def list_unlocked_achievements(
    student_id: UUID,
    language: Language,
    db: Session,
) -> tuple[UnlockedAchievement, ...]:
    """List active achievements and previously earned inactive achievements.

    Includes language-specific and global achievements. An achievement
    is unlocked only when a matching UserAchievement record exists.
    """
    definitions = db.scalars(select(Achievement).order_by(Achievement.achievement_key)).all()

    unlocks = db.scalars(
        select(UserAchievement).where(
            UserAchievement.user_id == student_id,
            (UserAchievement.language == language) | (UserAchievement.language.is_(None)),
        )
    ).all()

    unlock_by_key = {(unlock.achievement_id, unlock.language): unlock for unlock in unlocks}

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

    results: list[UnlockedAchievement] = []

    for achievement in definitions:
        is_global = achievement.category == "streak"
        achievement_language = None if is_global else language

        unlock = unlock_by_key.get((achievement.id, achievement_language))

        # Hide inactive definitions unless the user already earned them.
        if not achievement.is_active and unlock is None:
            continue

        condition = achievement.unlock_condition
        progress: AchievementProgress | None = None

        if condition.get("type") == "locations_completed":
            threshold = condition.get("threshold")

            if type(threshold) is int and threshold > 0:
                progress = AchievementProgress(
                    current=completed_count,
                    required=threshold,
                )

        results.append(
            UnlockedAchievement(
                achievement_key=achievement.achievement_key,
                category=achievement.category,
                icon_key=achievement.icon_key,
                language=achievement_language,
                unlocked=unlock is not None,
                unlocked_at=unlock.unlocked_at if unlock else None,
                progress=progress,
            )
        )

    # Earned achievements first, newest to oldest.
    # Locked achievements follow in alphabetical order.
    results.sort(
        key=lambda item: (
            not item.unlocked,
            -item.unlocked_at.timestamp() if item.unlocked_at is not None else 0,
            item.achievement_key,
        )
    )

    return tuple(results)
