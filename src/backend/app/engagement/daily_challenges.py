"""Generate three daily challenges per student using Vancouver calendar dates.

The generator locks the student row; the caller owns the database transaction.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement.streaks import vancouver_date
from app.models.accounts import User
from app.models.engagement import DailyChallenge
from app.models.enums import DailyChallengeType


def ensure_daily_challenges(
    student_id: UUID,
    db: Session,
    *,
    now: datetime | None = None,
) -> tuple[DailyChallenge, ...]:
    """Return exactly three challenges for the student's local day.

    Existing challenges are reused without resetting their progress.
    A student row lock serializes concurrent generation requests.
    The caller owns the transaction.
    """
    today = vancouver_date(now)

    db.flush()
    locked_user = db.scalar(
        select(User).where(User.id == student_id).with_for_update(key_share=False)
    )
    if locked_user is None:
        raise ValueError("Student not found")

    existing = tuple(
        db.scalars(
            select(DailyChallenge)
            .where(
                DailyChallenge.user_id == student_id,
                DailyChallenge.challenge_date == today,
            )
            .order_by(DailyChallenge.slot)
        )
    )

    if existing:
        if len(existing) != 3 or {item.slot for item in existing} != {1, 2, 3}:
            raise ValueError("Incomplete daily challenge set")
        return existing

    definitions = (
        (1, DailyChallengeType.COMPLETION, 1, 50, "complete_1_location"),
        (2, DailyChallengeType.COMPLETION, 3, 100, "complete_3_locations"),
        (3, DailyChallengeType.DIFFICULTY, 1, 100, "complete_1_hard_location"),
    )

    challenges = tuple(
        DailyChallenge(
            user_id=student_id,
            challenge_date=today,
            slot=slot,
            challenge_type=challenge_type.value,
            params={"difficulty": "hard"}
            if challenge_type == DailyChallengeType.DIFFICULTY
            else {},
            description_key=description_key,
            reward_points=reward_points,
            progress_current=0,
            progress_required=required,
            completed_at=None,
        )
        for slot, challenge_type, required, reward_points, description_key in definitions
    )

    db.add_all(challenges)
    db.flush()
    return challenges
