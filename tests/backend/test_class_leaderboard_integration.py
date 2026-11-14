"""Integration tests for A14 leaderboard updates from finalized gameplay."""

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.game.scoring import score_finalized_location
from app.models.accounts import User
from app.models.engagement import Class, ClassEnrollment, PointsLedger
from app.models.enums import LocationStatus
from app.models.game import GameSession, SessionLocation


def test_class_game_completion_updates_leaderboard(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    school_class = make_class()
    student = make_user()
    make_enrollment(student, school_class)
    game = make_game_session(student=student, class_id=school_class.id)
    location = make_session_location(
        game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )
    url = f"/api/v1/classes/{school_class.id}/leaderboard"

    before = client.get(url, headers=auth_headers(student))
    assert before.status_code == 200, before.text
    assert before.json()["items"][0]["total_points"] == 0

    result = score_finalized_location(game, location, db)
    assert result.location_points_awarded == 100

    after = client.get(url, headers=auth_headers(student))
    assert after.status_code == 200, after.text
    assert after.json()["items"] == [
        {
            "rank": 1,
            "student_id": str(student.id),
            "username": student.username,
            "total_points": 100,
        }
    ]

    awards = db.scalars(
        select(PointsLedger).where(
            PointsLedger.user_id == student.id,
            PointsLedger.source_ref == str(location.id),
        )
    ).all()
    assert len(awards) == 1
    assert awards[0].class_id == school_class.id


def test_repeated_game_scoring_does_not_inflate_leaderboard(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    school_class = make_class()
    student = make_user()
    make_enrollment(student, school_class)
    game = make_game_session(student=student, class_id=school_class.id)
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

    response = client.get(
        f"/api/v1/classes/{school_class.id}/leaderboard",
        headers=auth_headers(student),
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["total_points"] == 100


def test_practice_game_completion_does_not_change_class_leaderboard(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    school_class = make_class()
    student = make_user()
    make_enrollment(student, school_class)
    practice_game = make_game_session(student=student)
    location = make_session_location(
        practice_game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )

    result = score_finalized_location(practice_game, location, db)
    assert result.location_points_awarded == 100

    response = client.get(
        f"/api/v1/classes/{school_class.id}/leaderboard",
        headers=auth_headers(student),
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["total_points"] == 0

    award = db.scalar(select(PointsLedger).where(PointsLedger.source_ref == str(location.id)))
    assert award is not None
    assert award.class_id is None
