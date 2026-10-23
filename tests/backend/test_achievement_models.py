"""Database tests for achievement definitions and unlocks (Issue #86)."""

from collections.abc import Callable

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.engagement import Achievement, UserAchievement
from app.models.enums import Language


EXPECTED_KEYS = {
    "first_step",
    "starter",
    "explorer",
    "campus_master",
    "week_warrior",
    "challenge_master",
    "perfect_session",
}


def test_seven_achievements_seeded(db: Session) -> None:
    achievements = db.scalars(select(Achievement)).all()

    assert {achievement.achievement_key for achievement in achievements} == EXPECTED_KEYS
    assert len(achievements) == 7
    assert all(achievement.is_active for achievement in achievements)


def test_invalid_achievement_category_rejected(db: Session) -> None:
    db.add(
        Achievement(
            achievement_key="invalid_category",
            category="invalid",
            unlock_condition={"type": "test"},
            icon_key="test",
        )
    )

    with pytest.raises(IntegrityError):
        db.flush()


def test_duplicate_global_unlock_rejected(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    student = make_user()
    achievement = db.scalar(
        select(Achievement).where(Achievement.achievement_key == "week_warrior")
    )
    assert achievement is not None

    db.add_all(
        [
            UserAchievement(
                user_id=student.id,
                achievement_id=achievement.id,
                language=None,
            ),
            UserAchievement(
                user_id=student.id,
                achievement_id=achievement.id,
                language=None,
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        db.flush()

def test_same_achievement_can_unlock_in_different_languages(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    student = make_user()
    achievement = db.scalar(
        select(Achievement).where(Achievement.achievement_key == "first_step")
    )
    assert achievement is not None

    db.add_all(
        [
            UserAchievement(
                user_id=student.id,
                achievement_id=achievement.id,
                language=Language.JA,
            ),
            UserAchievement(
                user_id=student.id,
                achievement_id=achievement.id,
                language=Language.FR,
            ),
        ]
    )
    db.flush()

@pytest.mark.parametrize(
    ("achievement_key", "language"),
    [
        ("first_step", None),
        ("challenge_master", None),
        ("week_warrior", Language.JA),
    ],
)
def test_invalid_achievement_language_rejected(
    db: Session,
    make_user: Callable[..., User],
    achievement_key: str,
    language: str | None,
) -> None:
    student = make_user()
    achievement = db.scalar(
        select(Achievement).where(Achievement.achievement_key == achievement_key)
    )
    assert achievement is not None

    db.add(
        UserAchievement(
            user_id=student.id,
            achievement_id=achievement.id,
            language=language,
        )
    )

    with pytest.raises(IntegrityError):
        db.flush()
