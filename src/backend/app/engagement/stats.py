"""Read-only per-language engagement statistics."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.engagement import Achievement, UserAchievement, UserLanguageStats
from app.models.enums import Language

LEVEL_THRESHOLDS = (500, 1500, 3000, 5000)
RECENT_BADGES_LIMIT = 5


@dataclass(frozen=True)
class RecentBadge:
    """An achievement recently unlocked by the user."""

    achievement_key: str
    icon_key: str
    language: Language | None
    unlocked_at: datetime


@dataclass(frozen=True)
class LanguageEngagementStats:
    """Cumulative points, level progress, and recent badges."""

    language: Language
    total_points: int
    level: int
    points_to_next_level: int | None
    recent_badges: tuple[RecentBadge, ...]


def level_for_points(total_points: int) -> int:
    """Derive a user's language level from cumulative points."""
    return 1 + sum(total_points >= threshold for threshold in LEVEL_THRESHOLDS)


def points_to_next_level(total_points: int) -> int | None:
    """Return points needed for the next level, or None at level five."""
    for threshold in LEVEL_THRESHOLDS:
        if total_points < threshold:
            return threshold - total_points
    return None


def get_language_engagement_stats(
    student_id: UUID,
    language: Language,
    db: Session,
) -> LanguageEngagementStats:
    """Read the user's language engagement without changing the database."""
    stats = db.get(UserLanguageStats, (student_id, language))
    points = stats.total_points if stats is not None else 0

    statement = (
        select(UserAchievement, Achievement)
        .join(Achievement, UserAchievement.achievement_id == Achievement.id)
        .where(
            UserAchievement.user_id == student_id,
            (
                (UserAchievement.language == language)
                | (UserAchievement.language.is_(None))
            ),
        )
        .order_by(
            UserAchievement.unlocked_at.desc(),
            UserAchievement.id.desc(),
        )
        .limit(RECENT_BADGES_LIMIT)
    )

    badges = tuple(
        RecentBadge(
            achievement_key=achievement.achievement_key,
            icon_key=achievement.icon_key,
            language=Language(unlock.language) if unlock.language is not None else None,
            unlocked_at=unlock.unlocked_at,
        )
        for unlock, achievement in db.execute(statement).all()
    )

    return LanguageEngagementStats(
        language=language,
        total_points=points,
        level=level_for_points(points),
        points_to_next_level=points_to_next_level(points),
        recent_badges=badges,
    )
