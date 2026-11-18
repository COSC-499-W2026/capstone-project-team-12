"""Read-only per-language engagement statistics."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.models.engagement import Achievement, PointsLedger, UserAchievement, UserLanguageStats
from app.models.enums import Language, PointsSourceType

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
            ((UserAchievement.language == language) | (UserAchievement.language.is_(None))),
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


@dataclass(frozen=True)
class GameEngagement:
    """What one game added to the student's engagement, for its summary (A23, #47)."""

    level: int  # in the game's language, as of the game's last location award
    level_up: bool  # whether the game's awards raised the level
    new_badges: tuple[RecentBadge, ...]  # unlocked by the game's locations, oldest first


def get_game_engagement(
    student_id: UUID,
    language: Language,
    session_location_ids: Collection[UUID],
    started_at: datetime,
    db: Session,
) -> GameEngagement:
    """Read what a game's finished locations added, without changing the database.

    A game's awards are its location rows in the points ledger (source_ref is the session
    location id). A badge is unlocked in the same transaction as the award that earned it, so its
    unlocked_at equals that award's created_at (both are the transaction's now()); that is how a
    badge is matched to the game, global badges included. The level counts the language's points
    up to the game's last award, so an old game shows the level it reached then, not today's.

    A game with no award yet (nothing completed) has the level the student had when it started
    (started_at, also the database's now()), no level_up and no badges.
    """
    refs = [str(location_id) for location_id in session_location_ids]
    award_times = sorted(
        set(
            db.scalars(
                select(PointsLedger.created_at).where(
                    PointsLedger.user_id == student_id,
                    PointsLedger.source_type == PointsSourceType.LOCATION,
                    PointsLedger.source_ref.in_(refs),
                )
            )
        )
    )
    if not award_times:
        before_game = _points(student_id, language, PointsLedger.created_at < started_at, db)
        return GameEngagement(level=level_for_points(before_game), level_up=False, new_badges=())

    before = _points(student_id, language, PointsLedger.created_at < award_times[0], db)
    after = _points(student_id, language, PointsLedger.created_at <= award_times[-1], db)
    statement = (
        select(UserAchievement, Achievement)
        .join(Achievement, UserAchievement.achievement_id == Achievement.id)
        .where(
            UserAchievement.user_id == student_id,
            UserAchievement.unlocked_at.in_(award_times),
            (UserAchievement.language == language) | UserAchievement.language.is_(None),
        )
        .order_by(UserAchievement.unlocked_at, Achievement.achievement_key)
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
    level = level_for_points(after)
    return GameEngagement(
        level=level,
        level_up=level > level_for_points(before),
        new_badges=badges,
    )


def _points(student_id: UUID, language: Language, until: ColumnElement[bool], db: Session) -> int:
    """The student's ledger points in the language, from awards matching `until`."""
    total = db.scalar(
        select(func.coalesce(func.sum(PointsLedger.points), 0)).where(
            PointsLedger.user_id == student_id, PointsLedger.language == language, until
        )
    )
    return int(total or 0)
