"""Integration tests for A14 leaderboard updates from finalized gameplay."""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.game.scoring import score_finalized_location
from app.models.accounts import User
from app.models.engagement import Class, ClassEnrollment, PointsLedger, UserLanguageStats
from app.models.enums import LocationStatus, PointsSourceType
from app.models.game import GameSession, SessionLocation


@pytest.mark.parametrize(
    ("practice", "retry"),
    [(False, False), (False, True), (True, False)],
    ids=["class-completion", "class-retry", "practice-excluded"],
)
def test_finalized_game_updates_class_leaderboard(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    auth_headers: Callable[[User], dict[str, str]],
    practice: bool,
    retry: bool,
) -> None:
    school_class = make_class()
    student = make_user()
    make_enrollment(student, school_class)
    game_class_id = None if practice else school_class.id
    game = make_game_session(student=student, class_id=game_class_id)
    location = make_session_location(
        game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )
    # Same-language Practice history must never contribute to the class leaderboard.
    db.add_all(
        [
            PointsLedger(
                user_id=student.id,
                source_type=PointsSourceType.LOCATION,
                source_ref="previous-practice-location",
                class_id=None,
                language=game.target_language,
                points=200,
            ),
            UserLanguageStats(user_id=student.id, language=game.target_language, total_points=200),
        ]
    )
    db.flush()

    url = f"/api/v1/classes/{school_class.id}/leaderboard"
    headers = auth_headers(student)
    before = client.get(url, headers=headers)
    assert before.status_code == 200, before.text
    assert before.json()["items"][0]["total_points"] == 0

    class_points = select(func.coalesce(func.sum(PointsLedger.points), 0)).where(
        PointsLedger.user_id == student.id,
        PointsLedger.class_id == school_class.id,
    )
    first = score_finalized_location(game, location, db)
    assert first.location_points_awarded == 100
    expected = db.scalar(class_points)
    assert expected is not None
    if practice:
        assert expected == 0
    else:
        assert expected >= first.location_points_awarded

    if retry:
        second = score_finalized_location(game, location, db)
        assert second.location_points_awarded == 0
        assert db.scalar(class_points) == expected

    after = client.get(url, headers=headers)
    assert after.status_code == 200, after.text
    assert after.json()["items"] == [
        {"rank": 1, "username": student.username, "total_points": expected, "is_me": True},
    ]

    awards = db.scalars(
        select(PointsLedger).where(
            PointsLedger.user_id == student.id,
            PointsLedger.source_type == PointsSourceType.LOCATION,
            PointsLedger.source_ref == str(location.id),
        )
    ).all()
    assert len(awards) == 1
    assert awards[0].class_id == game_class_id
