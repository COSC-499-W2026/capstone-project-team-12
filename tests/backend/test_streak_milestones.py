"""Tests for A13 streak milestone rewards."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement.interface import apply_location_result
from app.engagement.streaks import app_calendar_date, award_streak_milestones, record_active_day
from app.models.accounts import User
from app.models.engagement import (
    Achievement,
    PointsLedger,
    UserAchievement,
    UserLanguageStats,
    UserStats,
)
from app.models.enums import Difficulty, Language, LocationStatus, PointsSourceType
from app.models.game import GameSession, SessionLocation


def _milestone_awards(db: Session, student_id: UUID) -> list[PointsLedger]:
    return db.scalars(
        select(PointsLedger).where(
            PointsLedger.user_id == student_id,
            PointsLedger.source_type == PointsSourceType.STREAK_MILESTONE,
        )
    ).all()


def _prepare_completed_location(
    db: Session,
    student: User,
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    streak_days: int,
    existing_awards: tuple[tuple[int, int], ...] = (),
) -> tuple[GameSession, SessionLocation]:
    db.add(
        UserStats(
            user_id=student.id,
            current_streak_days=streak_days - 1,
            best_streak_days=streak_days - 1,
            last_active_date=app_calendar_date() - timedelta(days=1),
        )
    )
    for milestone, points in existing_awards:
        db.add(
            PointsLedger(
                user_id=student.id,
                source_type=PointsSourceType.STREAK_MILESTONE,
                source_ref=str(milestone),
                class_id=None,
                language=Language.JA,
                points=points,
            )
        )
    db.flush()
    game = make_game_session(student=student, difficulty=Difficulty.BASIC)
    location = make_session_location(
        game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )
    return game, location


@pytest.mark.parametrize(
    ("streak_days", "existing_awards", "expected_count", "expected_points"),
    [(7, (), 1, 100), (30, ((7, 100),), 2, 300)],
)
def test_streak_milestones_are_awarded_once(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    streak_days: int,
    existing_awards: tuple[tuple[int, int], ...],
    expected_count: int,
    expected_points: int,
) -> None:
    student = make_user()
    game, location = _prepare_completed_location(
        db, student, make_game_session, make_session_location, streak_days, existing_awards
    )
    first = apply_location_result(
        location.id,
        student.id,
        completed=True,
        target_language=Language(game.target_language),
        class_id=game.class_id,
        db=db,
    )
    assert first.streak_days == streak_days
    awards = _milestone_awards(db, student.id)
    assert len(awards) == expected_count
    milestone = next(award for award in awards if award.source_ref == str(streak_days))
    assert milestone.points == expected_points
    assert milestone.language == game.target_language
    assert milestone.class_id == game.class_id

    language_stats = db.get(UserLanguageStats, (student.id, Language(game.target_language)))
    assert language_stats is not None
    assert language_stats.total_points == first.total_points

    if streak_days == 7:
        assert first.total_points >= 200
        week_warrior = next(
            (
                badge
                for badge in db.scalars(select(Achievement)).all()
                if badge.unlock_condition.get("type") == "streak_days"
                and badge.unlock_condition.get("threshold") == 7
            ),
            None,
        )
        assert week_warrior is not None
        assert week_warrior.achievement_key in first.achievements_unlocked
        unlocks = db.scalars(
            select(UserAchievement).where(
                UserAchievement.user_id == student.id,
                UserAchievement.achievement_id == week_warrior.id,
            )
        ).all()
        assert len(unlocks) == 1
        assert unlocks[0].language is None

    second = apply_location_result(
        location.id,
        student.id,
        completed=True,
        target_language=Language(game.target_language),
        class_id=game.class_id,
        db=db,
    )
    assert second.location_points_awarded == 0
    assert second.total_points == first.total_points
    assert len(_milestone_awards(db, student.id)) == expected_count
    if streak_days == 7:
        assert week_warrior.achievement_key not in second.achievements_unlocked


def test_streak_below_seven_days_awards_no_milestone(
    db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user()
    awarded = award_streak_milestones(student.id, 6, language=Language.JA, class_id=None, db=db)
    assert awarded == 0
    assert _milestone_awards(db, student.id) == []


def test_missed_day_resets_streak_but_preserves_best(
    db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user()
    now = datetime(2026, 11, 14, 20, tzinfo=UTC)
    today = app_calendar_date(now)
    db.add(
        UserStats(
            user_id=student.id,
            current_streak_days=5,
            best_streak_days=8,
            last_active_date=today - timedelta(days=2),
        )
    )
    db.flush()
    streak = record_active_day(student.id, db, now=now)
    stats = db.get(UserStats, student.id)
    assert streak == 1
    assert stats is not None
    assert stats.current_streak_days == 1
    assert stats.best_streak_days == 8
    assert stats.last_active_date == today


def test_both_milestones_are_awarded_when_first_reached_at_thirty_days(
    db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user()
    awarded = award_streak_milestones(student.id, 30, language=Language.JA, class_id=None, db=db)
    awards = _milestone_awards(db, student.id)
    assert awarded == 400
    assert len(awards) == 2
    assert {award.source_ref for award in awards} == {"7", "30"}
    assert sum(award.points for award in awards) == 400


def test_existing_milestones_are_not_awarded_again(
    db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user()
    for milestone, points in ((7, 100), (30, 300)):
        db.add(
            PointsLedger(
                user_id=student.id,
                source_type=PointsSourceType.STREAK_MILESTONE,
                source_ref=str(milestone),
                class_id=None,
                language=Language.JA,
                points=points,
            )
        )
    db.flush()
    awarded = award_streak_milestones(student.id, 30, language=Language.FR, class_id=None, db=db)
    assert awarded == 0
    assert len(_milestone_awards(db, student.id)) == 2


def test_milestone_is_global_across_languages(db: Session, make_user: Callable[..., User]) -> None:
    student = make_user()
    first = award_streak_milestones(student.id, 7, language=Language.JA, class_id=None, db=db)
    second = award_streak_milestones(student.id, 7, language=Language.FR, class_id=None, db=db)
    awards = _milestone_awards(db, student.id)
    assert first == 100
    assert second == 0
    assert len(awards) == 1
    assert awards[0].language == Language.JA


def test_milestone_awards_roll_back_with_transaction(
    db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user()
    db.flush()
    savepoint = db.begin_nested()
    awarded = award_streak_milestones(student.id, 7, language=Language.JA, class_id=None, db=db)
    assert awarded == 100
    savepoint.rollback()
    db.expire_all()
    assert _milestone_awards(db, student.id) == []
    assert db.get(UserLanguageStats, (student.id, Language.JA)) is None


@pytest.mark.parametrize(
    ("days_ago", "visible_streak"),
    [(1, 5), (2, 0)],
    ids=["yesterday", "expired"],
)
def test_failed_location_reports_streak_without_changing_it(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    days_ago: int,
    visible_streak: int,
) -> None:
    student = make_user()
    last_active = app_calendar_date() - timedelta(days=days_ago)
    stats = UserStats(
        user_id=student.id,
        current_streak_days=5,
        best_streak_days=8,
        last_active_date=last_active,
    )
    db.add(stats)
    game = make_game_session(student=student, difficulty=Difficulty.BASIC)
    location = make_session_location(game, position=1, status=LocationStatus.FAILED)

    result = apply_location_result(
        location.id,
        student.id,
        completed=False,
        target_language=Language(game.target_language),
        class_id=game.class_id,
        db=db,
    )

    assert result.streak_days == visible_streak
    db.refresh(stats)
    assert (stats.current_streak_days, stats.best_streak_days, stats.last_active_date) == (
        5,
        8,
        last_active,
    )
