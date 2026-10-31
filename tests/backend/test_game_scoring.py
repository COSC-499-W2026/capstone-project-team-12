"""Integration tests for A12 gameplay scoring."""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.game.scoring import score_finalized_location
from app.models.accounts import User
from app.models.engagement import PointsLedger, UserLanguageStats
from app.models.enums import Language, LocationStatus, SkipReason
from app.models.game import GameSession, SessionLocation


def test_completed_location_awards_points(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    game = make_game_session(student=student)
    location = make_session_location(
        game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    result = score_finalized_location(game, location, db)

    assert result.location_points_awarded == 100
    assert location.points_awarded == 100
    assert game.total_score == 100

    stats = db.get(UserLanguageStats, (student.id, Language.JA))
    assert stats is not None
    assert stats.total_points == 100


def test_duplicate_scoring_preserves_points(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    game = make_game_session(student=student)
    location = make_session_location(
        game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    first = score_finalized_location(game, location, db)
    second = score_finalized_location(game, location, db)

    assert first.location_points_awarded == 100
    assert second.location_points_awarded == 0
    assert location.points_awarded == 100
    assert game.total_score == 100

    awards = db.scalars(
        select(PointsLedger).where(
            PointsLedger.user_id == student.id,
            PointsLedger.source_ref == str(location.id),
        )
    ).all()

    assert len(awards) == 1


@pytest.mark.parametrize("status", [LocationStatus.FAILED, LocationStatus.SKIPPED])
def test_unsuccessful_location_awards_zero(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    status: LocationStatus,
) -> None:
    student = make_user()
    game = make_game_session(student=student)

    location = make_session_location(
        game,
        position=1,
        status=status,
        **({"skip_reason": SkipReason.SESSION_ENDED} if status == LocationStatus.SKIPPED else {}),
    )

    result = score_finalized_location(game, location, db)

    assert result.location_points_awarded == 0
    assert location.points_awarded == 0
    assert game.total_score == 0


def test_pending_location_is_rejected(
    db: Session,
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    game = make_game_session()
    location = make_session_location(game, position=1)

    with pytest.raises(ValueError, match="final status"):
        score_finalized_location(game, location, db)


def test_location_from_another_game_is_rejected(
    db: Session,
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    first_game = make_game_session()
    second_game = make_game_session()

    location = make_session_location(
        second_game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    with pytest.raises(ValueError, match="does not belong"):
        score_finalized_location(first_game, location, db)


def test_multiple_locations_keep_game_total_consistent(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    game = make_game_session(student=student)

    first_location = make_session_location(
        game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )
    second_location = make_session_location(
        game,
        position=2,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    score_finalized_location(game, first_location, db)
    score_finalized_location(game, second_location, db)

    assert game.total_score == 200

    stats = db.get(UserLanguageStats, (student.id, Language.JA))
    assert stats is not None
    assert stats.total_points == 200

    score_finalized_location(game, first_location, db)
    score_finalized_location(game, second_location, db)

    assert game.total_score == 200
    assert stats.total_points == 200

    awards = db.scalars(
        select(PointsLedger).where(
            PointsLedger.user_id == student.id,
        )
    ).all()
    assert len(awards) == 2


def test_scoring_rolls_back_with_transaction(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    student = make_user()
    game = make_game_session(student=student)
    location = make_session_location(
        game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    # Save the fixture setup so only the scoring operation is rolled back.
    db.flush()
    savepoint = db.begin_nested()

    score_finalized_location(game, location, db)

    assert game.total_score == 100
    assert (
        db.scalar(
            select(PointsLedger.id).where(
                PointsLedger.user_id == student.id,
                PointsLedger.source_ref == str(location.id),
            )
        )
        is not None
    )

    savepoint.rollback()
    db.expire_all()

    persisted_game = db.get(GameSession, game.id)
    assert persisted_game is not None
    assert persisted_game.total_score == 0
    assert (
        db.scalar(
            select(PointsLedger.id).where(
                PointsLedger.user_id == student.id,
                PointsLedger.source_ref == str(location.id),
            )
        )
        is None
    )
    assert db.get(UserLanguageStats, (student.id, Language.JA)) is None
