"""Generate three daily challenges per student using Vancouver calendar dates.

This slice uses fixed targets. The specification calls for daily variety with
60% completion and 40% difficulty weighting in the final generator.

The generator locks the student row; the caller owns the database transaction.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement.streaks import app_calendar_date
from app.errors import AppError, ErrorCode
from app.models.engagement import DailyChallenge
from app.models.enums import DailyChallengeType, Difficulty
from app.services.locks import lock_user


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
    today = app_calendar_date(now)

    if lock_user(student_id, db) is None:
        raise AppError(ErrorCode.NOT_FOUND)

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

    by_slot = {challenge.slot: challenge for challenge in existing}
    definitions = (
        (1, DailyChallengeType.COMPLETION, 1, 50, "complete_1_location"),
        (2, DailyChallengeType.COMPLETION, 3, 100, "complete_3_locations"),
        (3, DailyChallengeType.DIFFICULTY, 1, 100, "complete_1_challenging_location"),
    )

    # Keep progress and IDs when filling missing slots or updating old localisation data.
    for challenge in existing:
        params = {"count": challenge.progress_required, **challenge.params}
        if params.get("difficulty") == "hard":
            params["difficulty"] = Difficulty.CHALLENGING
        if challenge.description_key == "complete_1_hard_location":
            challenge.description_key = "complete_1_challenging_location"
        if params != challenge.params:
            challenge.params = params

    new_challenges = [
        DailyChallenge(
            user_id=student_id,
            challenge_date=today,
            slot=slot,
            challenge_type=challenge_type.value,
            params={"count": required, "difficulty": Difficulty.CHALLENGING}
            if challenge_type == DailyChallengeType.DIFFICULTY
            else {"count": required},
            description_key=description_key,
            reward_points=reward_points,
            progress_current=0,
            progress_required=required,
        )
        for slot, challenge_type, required, reward_points, description_key in definitions
        if slot not in by_slot
    ]
    if new_challenges:
        db.add_all(new_challenges)
        db.flush()
        by_slot.update((challenge.slot, challenge) for challenge in new_challenges)
    return tuple(by_slot[slot] for slot in (1, 2, 3))
