"""Badge unlocking and achievement retrieval helpers (Issue #86)."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.content.interface import eligible_location_ids
from app.models.engagement import Achievement, PointsLedger, UserAchievement
from app.models.enums import Difficulty, Language, LocationStatus, PointsSourceType, SkipReason
from app.models.game import GameSession, SessionLocation


def _challenging_completion_count(student_id: UUID, language: str, db: Session) -> int:
    """Count completed location results in challenging games for one student and language."""
    return (
        db.scalar(
            select(func.count(SessionLocation.id))
            .join(GameSession, GameSession.id == SessionLocation.session_id)
            .where(
                GameSession.student_id == student_id,
                GameSession.target_language == language,
                GameSession.difficulty == Difficulty.CHALLENGING,
                SessionLocation.status == LocationStatus.COMPLETED,
            )
        )
        or 0
    )


def _is_perfect_session(session_id: UUID, required: int, db: Session) -> bool:
    """Check the live positions of this session, including the just-scored result."""
    total, completed = db.execute(
        select(
            func.count(),
            func.count().filter(SessionLocation.status == LocationStatus.COMPLETED),
        ).where(
            SessionLocation.session_id == session_id,
            SessionLocation.skip_reason.is_distinct_from(SkipReason.SHUFFLED),
        )
    ).one()
    return total == completed == required


def _campus_progress(student_id: UUID, language: Language, db: Session) -> tuple[int, int]:
    """Count distinct completed locations among the language's current drawable content."""
    playable = eligible_location_ids(language, None, db)
    if not playable:
        return 0, 0
    completed = (
        db.scalar(
            select(func.count(func.distinct(SessionLocation.location_id)))
            .join(GameSession, GameSession.id == SessionLocation.session_id)
            .where(
                GameSession.student_id == student_id,
                GameSession.target_language == language,
                SessionLocation.status == LocationStatus.COMPLETED,
                SessionLocation.location_id.in_(playable),
            )
        )
        or 0
    )
    return completed, len(playable)


def _completion_condition_met(
    condition: dict[str, object],
    student_id: UUID,
    language: str,
    completed_count: int,
    session_id: UUID | None,
    db: Session,
) -> bool:
    condition_type = condition.get("type")
    if condition_type == "all_playable_locations":
        completed, required = _campus_progress(student_id, Language(language), db)
        return required > 0 and completed == required

    threshold = condition.get("locations" if condition_type == "perfect_session" else "threshold")
    if type(threshold) is not int or threshold < 1:
        return False
    if condition_type == "locations_completed":
        return completed_count >= threshold
    if condition_type == "challenging_locations_completed":
        return _challenging_completion_count(student_id, language, db) >= threshold
    return (
        condition_type == "perfect_session"
        and session_id is not None
        and _is_perfect_session(session_id, threshold, db)
    )


def unlock_completion_badges(
    student_id: UUID,
    language: str,
    db: Session,
    *,
    session_id: UUID | None = None,
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
        if not _completion_condition_met(
            achievement.unlock_condition, student_id, language, completed_count, session_id, db
        ):
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


def unlock_streak_badges(
    student_id: UUID,
    streak_days: int,
    db: Session,
) -> tuple[str, ...]:
    """Unlock eligible global streak achievements once per student.

    Streak achievements are stored with language=None so they apply
    across all languages.

    Called while the student's row lock is held. Does not commit;
    the caller owns the transaction.
    """
    definitions = db.scalars(
        select(Achievement).where(
            Achievement.is_active.is_(True),
            Achievement.category == "streak",
        )
    ).all()

    unlocked: list[str] = []

    for achievement in definitions:
        condition = achievement.unlock_condition

        if condition.get("type") != "streak_days":
            continue

        threshold = condition.get("threshold")
        if type(threshold) is not int or threshold < 1:
            continue

        if streak_days < threshold:
            continue

        existing = db.scalar(
            select(UserAchievement.id).where(
                UserAchievement.user_id == student_id,
                UserAchievement.achievement_id == achievement.id,
                UserAchievement.language.is_(None),
            )
        )
        if existing is not None:
            continue

        db.add(
            UserAchievement(
                user_id=student_id,
                achievement_id=achievement.id,
                language=None,
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
            in_language_or_global(language),
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

        if condition.get("type") == "all_playable_locations":
            current, required = _campus_progress(student_id, language, db)
            if required > 0:
                progress = AchievementProgress(current=current, required=required)
        threshold = condition.get("threshold")
        if type(threshold) is int and threshold > 0:
            if condition.get("type") == "locations_completed":
                progress = AchievementProgress(current=completed_count, required=threshold)
            elif condition.get("type") == "challenging_locations_completed":
                progress = AchievementProgress(
                    current=_challenging_completion_count(student_id, language, db),
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


def in_language_or_global(language: Language) -> ColumnElement[bool]:
    """Unlocks in the language, and global ones."""
    return (UserAchievement.language == language) | UserAchievement.language.is_(None)
