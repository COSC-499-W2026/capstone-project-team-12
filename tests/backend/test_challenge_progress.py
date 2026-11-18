"""Focused tests for A13 daily challenge progress and rewards."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement.challenge_progress import record_challenge_progress
from app.engagement.daily_challenges import ensure_daily_challenges
from app.engagement.interface import apply_location_result
from app.models.accounts import User
from app.models.engagement import DailyChallenge, PointsLedger, UserLanguageStats
from app.models.enums import Difficulty, Language, LocationStatus
from app.models.game import GameSession, SessionLocation


def test_basic_completion_and_retry(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    game = make_game_session(student=student, difficulty=Difficulty.BASIC)
    location = make_session_location(
        game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    first = apply_location_result(
        location.id,
        student.id,
        completed=True,
        target_language=Language(game.target_language),
        class_id=game.class_id,
        db=db,
    )
    second = apply_location_result(
        location.id,
        student.id,
        completed=True,
        target_language=Language(game.target_language),
        class_id=game.class_id,
        db=db,
    )

    assert first.location_points_awarded == 100
    assert first.total_points == 150
    assert len(first.challenges_completed) == 1
    assert second.location_points_awarded == 0
    assert second.challenges_completed == ()
    assert second.total_points == 150

    challenges = ensure_daily_challenges(student.id, db)
    assert [c.progress_current for c in challenges] == [1, 1, 0]

    bonuses = db.scalars(
        select(PointsLedger).where(
            PointsLedger.user_id == student.id,
            PointsLedger.source_type == "daily_challenge",
        )
    ).all()
    assert len(bonuses) == 1
    assert bonuses[0].points == 50


@pytest.mark.parametrize(
    ("difficulty", "expected_progress", "expected_bonus"),
    [
        (Difficulty.BASIC, 0, 50),
        (Difficulty.ADVANCING, 0, 50),
        (Difficulty.CHALLENGING, 1, 150),
    ],
)
def test_difficulty_eligibility(
    db: Session,
    make_user: Callable[..., User],
    difficulty: Difficulty,
    expected_progress: int,
    expected_bonus: int,
) -> None:
    student = make_user()

    completed = record_challenge_progress(
        student.id,
        difficulty=difficulty,
        language=Language.JA,
        class_id=None,
        db=db,
    )

    challenges = ensure_daily_challenges(student.id, db)

    assert challenges[2].progress_current == expected_progress
    assert sum(c.reward_points for c in challenges if c.completed_at) == expected_bonus
    assert len(completed) == (2 if expected_progress else 1)

    stats = db.get(UserLanguageStats, (student.id, Language.JA))
    assert stats is not None
    assert stats.total_points == expected_bonus


def test_progress_uses_configured_difficulty_and_count(
    db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user()
    now = datetime.now(UTC)
    challenge = ensure_daily_challenges(student.id, db, now=now)[2]
    challenge.params = {"difficulty": Difficulty.ADVANCING, "count": 2}

    for difficulty, expected_progress in (
        (Difficulty.CHALLENGING, 0),
        (Difficulty.ADVANCING, 1),
        (Difficulty.ADVANCING, 2),
    ):
        completed = record_challenge_progress(
            student.id,
            difficulty=difficulty,
            language=Language.JA,
            class_id=None,
            db=db,
            now=now,
        )
        assert challenge.progress_current == expected_progress
        assert (str(challenge.id) in completed) == (expected_progress == 2)


def test_three_completions_and_global_progress(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    student = make_user()
    languages = (Language.JA, Language.FR, Language.JA)

    for language in languages:
        record_challenge_progress(
            student.id,
            difficulty=Difficulty.BASIC,
            language=language,
            class_id=None,
            db=db,
        )

    challenges = ensure_daily_challenges(student.id, db)

    assert [c.progress_current for c in challenges] == [1, 3, 0]
    assert challenges[1].completed_at is not None

    japanese_stats = db.get(UserLanguageStats, (student.id, Language.JA))
    french_stats = db.get(UserLanguageStats, (student.id, Language.FR))

    assert japanese_stats is not None
    assert japanese_stats.total_points == 150
    assert french_stats is None

    awards = db.scalars(
        select(PointsLedger).where(
            PointsLedger.user_id == student.id,
            PointsLedger.source_type == "daily_challenge",
        )
    ).all()

    assert sorted(a.points for a in awards) == [50, 100]


def test_vancouver_midnight_creates_fresh_challenges(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    student = make_user()

    # Nov 14, 2026, 06:59 UTC is Nov 13 in Vancouver;
    # 07:01 UTC is Nov 14 in Vancouver.
    before = datetime(2026, 11, 14, 6, 59, tzinfo=UTC)
    after = before + timedelta(minutes=2)

    record_challenge_progress(
        student.id,
        difficulty=Difficulty.BASIC,
        language=Language.JA,
        class_id=None,
        db=db,
        now=before,
    )

    yesterday = ensure_daily_challenges(student.id, db, now=before)
    today = ensure_daily_challenges(student.id, db, now=after)

    assert yesterday[0].challenge_date != today[0].challenge_date
    assert yesterday[0].progress_current == 1
    assert all(challenge.progress_current == 0 for challenge in today)
    assert len({challenge.id for challenge in (*yesterday, *today)}) == 6


def test_progress_and_bonus_rollback_together(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    student = make_user()
    db.flush()

    savepoint = db.begin_nested()

    record_challenge_progress(
        student.id,
        difficulty=Difficulty.CHALLENGING,
        language=Language.JA,
        class_id=None,
        db=db,
    )

    assert (
        db.scalars(
            select(PointsLedger).where(
                PointsLedger.user_id == student.id,
            )
        ).first()
        is not None
    )

    savepoint.rollback()
    db.expire_all()

    assert (
        db.scalars(
            select(DailyChallenge).where(
                DailyChallenge.user_id == student.id,
            )
        ).all()
        == []
    )
    assert (
        db.scalars(
            select(PointsLedger).where(
                PointsLedger.user_id == student.id,
            )
        ).all()
        == []
    )
    assert db.get(UserLanguageStats, (student.id, Language.JA)) is None
