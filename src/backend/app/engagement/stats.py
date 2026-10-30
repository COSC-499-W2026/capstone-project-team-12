"""Read-only per-language engagement statistics."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.engagement import UserLanguageStats
from app.models.enums import Language


@dataclass(frozen=True)
class LanguageEngagementStats:
    """Cumulative points and level for one student and language."""

    language: Language
    total_points: int
    level: int


def level_for_points(total_points: int) -> int:
    """Derive a student's language level from cumulative points."""
    return 1 + sum(total_points >= threshold for threshold in (500, 1500, 3000, 5000))


def get_language_engagement_stats(
    student_id: UUID,
    language: Language,
    db: Session,
) -> LanguageEngagementStats:
    """Read one student's language points without changing the database."""
    stats = db.get(UserLanguageStats, (student_id, language))
    points = stats.total_points if stats is not None else 0

    level = level_for_points(points)

    return LanguageEngagementStats(
        language=language,
        total_points=points,
        level=level,
    )
