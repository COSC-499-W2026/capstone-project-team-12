"""Tests for A13 streak milestone rewards."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement.interface import apply_location_result
from app.engagement.streaks import (
    award_streak_milestones,
    record_active_day,
    vancouver_date,
)
from app.models.accounts import User
from app.models.engagement import PointsLedger, UserLanguageStats, UserStats
from app.models.enums import (
    Difficulty,
    Language,
    LocationStatus,
    PointsSourceType,
)
from app.models.game import GameSession, SessionLocation


def test_seven_day_streak_awards_100_points_once(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    today = vancouver_date()

    db.add(
        UserStats(
            user_id=student.id,
            current_streak_days=6,
            best_streak_days=6,
            last_active_date=today - timedelta(days=1),
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

    first = apply_location_result(
        location.id,
        student.id,
        completed=True,
        target_language=Language(game.target_language),
        class_id=game.class_id,
        db=db,
    )

    assert first.streak_days == 7

    awards = db.scalars(
        select(PointsLedger).where(
            PointsLedger.user_id == student.id,
            PointsLedger.source_type == PointsSourceType.STREAK_MILESTONE,
        )
    ).all()

    assert len(awards) == 1
    assert awards[0].source_ref == "7"
    assert awards[0].points == 100
    assert awards[0].language == game.target_language
    assert awards[0].class_id == game.class_id

    total_after_first = first.total_points

    second = apply_location_result(
        location.id,
        student.id,
        completed=True,
        target_language=Language(game.target_language),
        class_id=game.class_id,
        db=db,
    )

    assert second.location_points_awarded == 0
    assert second.total_points == total_after_first

    awards_after_retry = db.scalars(
        select(PointsLedger).where(
            PointsLedger.user_id == student.id,
            PointsLedger.source_type == PointsSourceType.STREAK_MILESTONE,
        )
    ).all()

    assert len(awards_after_retry) == 1


def test_thirty_day_streak_awards_300_points_once(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    today = vancouver_date()

    db.add(
        UserStats(
            user_id=student.id,
            current_streak_days=29,
            best_streak_days=29,
            last_active_date=today - timedelta(days=1),
        )
    )

    db.add(
        PointsLedger(
            user_id=student.id,
            source_type=PointsSourceType.STREAK_MILESTONE,
            source_ref="7",
            class_id=None,
            language=Language.JA,
            points=100,
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

    first = apply_location_result(
        location.id,
        student.id,
        completed=True,
        target_language=Language(game.target_language),
        class_id=game.class_id,
        db=db,
    )

    assert first.streak_days == 30

    awards = db.scalars(
        select(PointsLedger)
        .where(
            PointsLedger.user_id == student.id,
            PointsLedger.source_type == PointsSourceType.STREAK_MILESTONE,
        )
        .order_by(PointsLedger.source_ref)
    ).all()

    assert len(awards) == 2

    seven_day_award = next(a for a in awards if a.source_ref == "7")
    thirty_day_award = next(a for a in awards if a.source_ref == "30")

    assert seven_day_award.points == 100
    assert thirty_day_award.points == 300

    assert thirty_day_award.language == game.target_language
    assert thirty_day_award.class_id == game.class_id

    total_after_first = first.total_points

    second = apply_location_result(
        location.id,
        student.id,
        completed=True,
        target_language=Language(game.target_language),
        class_id=game.class_id,
        db=db,
    )

    assert second.location_points_awarded == 0
    assert second.total_points == total_after_first

    awards_after_retry = db.scalars(
        select(PointsLedger).where(
            PointsLedger.user_id == student.id,
            PointsLedger.source_type == PointsSourceType.STREAK_MILESTONE,
        )
    ).all()

    assert len(awards_after_retry) == 2


def test_streak_below_seven_days_awards_no_milestone(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    student = make_user()

    awarded = award_streak_milestones(
        student.id,
        6,
        language=Language.JA,
        class_id=None,
        db=db,
    )

    assert awarded == 0

    awards = db.scalars(
        select(PointsLedger).where(
            PointsLedger.user_id == student.id,
            PointsLedger.source_type == PointsSourceType.STREAK_MILESTONE,
        )
    ).all()

    assert awards == []


def test_missed_day_resets_streak_but_preserves_best(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    student = make_user()
    now = datetime(2026, 11, 14, 20, tzinfo=UTC)
    today = vancouver_date(now)

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
    db: Session,
    make_user: Callable[..., User],
) -> None:
    student = make_user()

    awarded = award_streak_milestones(
        student.id,
        30,
        language=Language.JA,
        class_id=None,
        db=db,
    )

    assert awarded == 400

    awards = db.scalars(
        select(PointsLedger)
        .where(
            PointsLedger.user_id == student.id,
            PointsLedger.source_type == PointsSourceType.STREAK_MILESTONE,
        )
        .order_by(PointsLedger.source_ref)
    ).all()

    assert len(awards) == 2
    assert {award.source_ref for award in awards} == {"7", "30"}
    assert sum(award.points for award in awards) == 400


def test_existing_milestones_are_not_awarded_again(
    db: Session,
    make_user: Callable[..., User],
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

    awarded = award_streak_milestones(
        student.id,
        30,
        language=Language.FR,
        class_id=None,
        db=db,
    )

    assert awarded == 0

    awards = db.scalars(
        select(PointsLedger).where(
            PointsLedger.user_id == student.id,
            PointsLedger.source_type == PointsSourceType.STREAK_MILESTONE,
        )
    ).all()

    assert len(awards) == 2


def test_milestone_is_global_across_languages(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    student = make_user()

    first = award_streak_milestones(
        student.id,
        7,
        language=Language.JA,
        class_id=None,
        db=db,
    )

    second = award_streak_milestones(
        student.id,
        7,
        language=Language.FR,
        class_id=None,
        db=db,
    )

    assert first == 100
    assert second == 0

    awards = db.scalars(
        select(PointsLedger).where(
            PointsLedger.user_id == student.id,
            PointsLedger.source_type == PointsSourceType.STREAK_MILESTONE,
        )
    ).all()

    assert len(awards) == 1
    assert awards[0].language == Language.JA


def test_milestone_awards_roll_back_with_transaction(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    student = make_user()
    db.flush()

    savepoint = db.begin_nested()

    awarded = award_streak_milestones(
        student.id,
        7,
        language=Language.JA,
        class_id=None,
        db=db,
    )

    assert awarded == 100

    savepoint.rollback()
    db.expire_all()

    awards = db.scalars(
        select(PointsLedger).where(
            PointsLedger.user_id == student.id,
            PointsLedger.source_type == PointsSourceType.STREAK_MILESTONE,
        )
    ).all()

    assert awards == []

    stats = db.get(UserLanguageStats, (student.id, Language.JA))
    assert stats is None
