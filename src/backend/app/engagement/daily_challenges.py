"""Generate three daily challenges per student using the configured app timezone.

The generator locks the student row; the caller owns the database transaction.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from random import choice, choices
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement.streaks import app_calendar_date
from app.errors import AppError, ErrorCode
from app.models.engagement import DailyChallenge
from app.models.enums import DailyChallengeType, Difficulty
from app.services.locks import lock_user


@dataclass(frozen=True)
class ChallengeTarget:
    count: int
    difficulty: Difficulty | None
    reward: int
    description_key: str

    @property
    def key(self) -> tuple[int, Difficulty | None]:
        return self.count, self.difficulty


_TARGETS = (
    ChallengeTarget(1, None, 50, "complete_1_location"),
    ChallengeTarget(2, None, 50, "complete_2_locations"),
    ChallengeTarget(3, None, 100, "complete_3_locations"),
    ChallengeTarget(5, None, 100, "complete_5_locations"),
    ChallengeTarget(2, Difficulty.BASIC, 50, "complete_2_basic_locations"),
    ChallengeTarget(3, Difficulty.BASIC, 100, "complete_3_basic_locations"),
    ChallengeTarget(2, Difficulty.ADVANCING, 100, "complete_2_advancing_locations"),
    ChallengeTarget(1, Difficulty.CHALLENGING, 100, "complete_1_challenging_location"),
)


def _pick_target(available: list[ChallengeTarget]) -> ChallengeTarget:
    difficulty_based = choices((False, True), weights=(60, 40))[0]
    matching = [
        target for target in available if (target.difficulty is not None) == difficulty_based
    ]
    return choice(matching or available)


def _normalise_difficulty(difficulty: object) -> object:
    return Difficulty.CHALLENGING if difficulty == "hard" else difficulty


def _target_key(challenge: DailyChallenge) -> tuple[int, int | None]:
    difficulty = _normalise_difficulty(challenge.params.get("difficulty"))
    return challenge.progress_required, difficulty if isinstance(difficulty, int) else None


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
    # Keep progress and IDs when filling missing slots or updating old localisation data.
    for challenge in existing:
        params = {"count": challenge.progress_required, **challenge.params}
        if "difficulty" in params:
            params["difficulty"] = _normalise_difficulty(params["difficulty"])
        if challenge.description_key == "complete_1_hard_location":
            challenge.description_key = "complete_1_challenging_location"
        if params != challenge.params:
            challenge.params = params

    if len(by_slot) == 3:
        return tuple(by_slot[slot] for slot in (1, 2, 3))

    previous_targets = {
        _target_key(challenge)
        for challenge in db.scalars(
            select(DailyChallenge).where(
                DailyChallenge.user_id == student_id,
                DailyChallenge.challenge_date == today - timedelta(days=1),
            )
        )
    }
    used = {_target_key(challenge) for challenge in existing}
    new_challenges = []
    for slot in (1, 2, 3):
        if slot in by_slot:
            continue
        # If the first two targets match yesterday, the last draw must introduce a new one.
        excluded = previous_targets if len(by_slot) == 2 and used <= previous_targets else used
        target = _pick_target([target for target in _TARGETS if target.key not in excluded])
        params = {"count": target.count}
        challenge_type = DailyChallengeType.COMPLETION
        if target.difficulty is not None:
            params["difficulty"] = target.difficulty.value
            challenge_type = DailyChallengeType.DIFFICULTY
        challenge = DailyChallenge(
            user_id=student_id,
            challenge_date=today,
            slot=slot,
            challenge_type=challenge_type.value,
            params=params,
            description_key=target.description_key,
            reward_points=target.reward,
            progress_current=0,
            progress_required=target.count,
        )
        by_slot[slot] = challenge
        new_challenges.append(challenge)
        used.add(target.key)

    db.add_all(new_challenges)
    db.flush()
    return tuple(by_slot[slot] for slot in (1, 2, 3))
