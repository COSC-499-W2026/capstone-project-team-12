"""Track daily challenge progress and award completion bonuses.

The caller owns the transaction and must hold the student's row lock.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement.daily_challenges import ensure_daily_challenges
from app.models.engagement import PointsLedger, UserLanguageStats
from app.models.enums import DailyChallengeType, Language, PointsSourceType


def record_challenge_progress(
    student_id: UUID,
    *,
    difficulty: int,
    language: Language,
    class_id: UUID | None,
    db: Session,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """Advance today's challenges for one newly completed location.

    Return identifiers of newly completed challenges. The caller must
    ensure this location has not previously received completion points.
    """
    timestamp = now or datetime.now(UTC)
    challenges = ensure_daily_challenges(student_id, db, now=timestamp)
    newly_completed: list[str] = []

    for challenge in challenges:
        if challenge.completed_at is not None:
            continue

        qualifies = challenge.challenge_type == DailyChallengeType.COMPLETION or (
            challenge.challenge_type == DailyChallengeType.DIFFICULTY
            and difficulty == challenge.params.get("difficulty")
        )
        if not qualifies:
            continue

        count = challenge.params.get("count")
        if type(count) is int and count > 0:
            challenge.progress_required = max(count, challenge.progress_current)

        challenge.progress_current = min(
            challenge.progress_current + 1,
            challenge.progress_required,
        )

        if challenge.progress_current < challenge.progress_required:
            continue

        source_ref = str(challenge.id)
        existing_award = db.scalar(
            select(PointsLedger.id).where(
                PointsLedger.user_id == student_id,
                PointsLedger.source_type == PointsSourceType.DAILY_CHALLENGE,
                PointsLedger.source_ref == source_ref,
            )
        )
        if existing_award is not None:
            continue

        challenge.completed_at = timestamp
        db.add(
            PointsLedger(
                user_id=student_id,
                source_type=PointsSourceType.DAILY_CHALLENGE,
                source_ref=source_ref,
                class_id=class_id,
                language=language,
                points=challenge.reward_points,
            )
        )

        stats = db.get(UserLanguageStats, (student_id, language))
        if stats is None:
            db.add(
                UserLanguageStats(
                    user_id=student_id,
                    language=language,
                    total_points=challenge.reward_points,
                )
            )
        else:
            stats.total_points += challenge.reward_points

        newly_completed.append(source_ref)
        db.flush()

    return tuple(newly_completed)
