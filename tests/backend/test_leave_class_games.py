"""Tests for POST /api/v1/me/classes/{class_id}/leave."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.engagement import Class, ClassEnrollment
from app.models.enums import (
    Language,
    LocationStatus,
    SessionEndReason,
    SessionState,
    SkipReason,
)
from app.models.game import GameSession, SessionLocation


def leave_as(
    client: TestClient,
    user: User,
    class_id: str,
    auth_headers: Callable[[User], dict[str, str]],
) -> Any:
    """Send an authenticated leave-class request."""
    return client.post(
        f"/api/v1/me/classes/{class_id}/leave",
        headers=auth_headers(user),
    )


def test_leave_abandons_active_game_and_skips_unfinished_locations(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user()
    school_class = make_class()
    make_enrollment(student, school_class)

    game = make_game_session(
        student,
        class_id=school_class.id,
        target_language=school_class.target_language,
    )
    pending = make_session_location(game, 1)
    in_progress = make_session_location(
        game,
        2,
        status=LocationStatus.NAVIGATING,
    )

    response = leave_as(client, student, str(school_class.id), auth_headers)

    assert response.status_code == 204, response.text

    db.refresh(game)
    db.refresh(pending)
    db.refresh(in_progress)

    assert game.state == SessionState.ABANDONED
    assert game.end_reason == SessionEndReason.CLASS_CLOSED

    for location in (pending, in_progress):
        assert location.status == LocationStatus.SKIPPED
        assert location.skip_reason == SkipReason.SESSION_ENDED


def test_leave_abandons_paused_game(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    make_game_session: Callable[..., GameSession],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user()
    school_class = make_class()
    make_enrollment(student, school_class)

    game = make_game_session(
        student,
        class_id=school_class.id,
        target_language=school_class.target_language,
        state=SessionState.PAUSED,
        paused_at=datetime.now(UTC),
    )

    response = leave_as(client, student, str(school_class.id), auth_headers)

    assert response.status_code == 204, response.text

    db.refresh(game)
    assert game.state == SessionState.ABANDONED
    assert game.end_reason == SessionEndReason.CLASS_CLOSED
    assert game.paused_at is None


def test_leave_preserves_completed_game_and_points(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user()
    school_class = make_class()
    make_enrollment(student, school_class)

    game = make_game_session(
        student,
        class_id=school_class.id,
        target_language=school_class.target_language,
        state=SessionState.COMPLETED,
        completed_at=datetime.now(UTC),
        total_score=100,
    )
    location = make_session_location(
        game,
        1,
        status=LocationStatus.COMPLETED,
        completed_at=datetime.now(UTC),
        points_awarded=100,
    )

    response = leave_as(client, student, str(school_class.id), auth_headers)

    assert response.status_code == 204, response.text

    db.refresh(game)
    db.refresh(location)

    assert game.state == SessionState.COMPLETED
    assert game.end_reason is None
    assert game.total_score == 100
    assert location.status == LocationStatus.COMPLETED
    assert location.points_awarded == 100


def test_leave_preserves_completed_location_in_active_game(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user()
    school_class = make_class()
    make_enrollment(student, school_class)

    game = make_game_session(
        student,
        class_id=school_class.id,
        target_language=school_class.target_language,
        total_score=100,
    )
    completed = make_session_location(
        game,
        1,
        status=LocationStatus.COMPLETED,
        completed_at=datetime.now(UTC),
        points_awarded=100,
    )
    pending = make_session_location(game, 2)

    response = leave_as(client, student, str(school_class.id), auth_headers)

    assert response.status_code == 204, response.text

    db.refresh(game)
    db.refresh(completed)
    db.refresh(pending)

    assert game.state == SessionState.ABANDONED
    assert game.end_reason == SessionEndReason.CLASS_CLOSED
    assert game.total_score == 100

    assert completed.status == LocationStatus.COMPLETED
    assert completed.points_awarded == 100

    assert pending.status == LocationStatus.SKIPPED
    assert pending.skip_reason == SkipReason.SESSION_ENDED


def test_leave_does_not_affect_other_students_games(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    make_game_session: Callable[..., GameSession],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    leaving_student = make_user()
    other_student = make_user()
    school_class = make_class()

    make_enrollment(leaving_student, school_class)
    other_enrollment = make_enrollment(other_student, school_class)

    leaving_game = make_game_session(
        leaving_student,
        class_id=school_class.id,
        target_language=school_class.target_language,
    )
    other_game = make_game_session(
        other_student,
        class_id=school_class.id,
        target_language=school_class.target_language,
    )

    response = leave_as(client, leaving_student, str(school_class.id), auth_headers)

    assert response.status_code == 204, response.text

    db.refresh(leaving_game)
    db.refresh(other_game)
    db.refresh(other_enrollment)

    assert leaving_game.state == SessionState.ABANDONED
    assert other_game.state == SessionState.ACTIVE
    assert other_enrollment.is_active is True


def test_leave_does_not_affect_game_in_different_class(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    make_game_session: Callable[..., GameSession],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user()
    first_class = make_class(target_language=Language.FR)
    second_class = make_class(target_language=Language.JA)

    first_enrollment = make_enrollment(student, first_class)
    second_enrollment = make_enrollment(student, second_class)

    first_game = make_game_session(
        student,
        class_id=first_class.id,
        target_language=first_class.target_language,
    )

    # Close the first game before creating the student's second open game.
    first_game.state = SessionState.ABANDONED
    first_game.end_reason = SessionEndReason.CLASS_CLOSED
    db.flush()

    second_game = make_game_session(
        student,
        class_id=second_class.id,
        target_language=second_class.target_language,
        state=SessionState.PAUSED,
        paused_at=datetime.now(UTC),
    )

    response = leave_as(client, student, str(first_class.id), auth_headers)

    assert response.status_code == 204, response.text

    db.refresh(first_game)
    db.refresh(second_game)
    db.refresh(first_enrollment)
    db.refresh(second_enrollment)

    assert first_game.state == SessionState.ABANDONED
    assert first_game.end_reason == SessionEndReason.CLASS_CLOSED

    assert second_game.state == SessionState.PAUSED
    assert second_game.end_reason is None

    assert first_enrollment.is_active is False
    assert second_enrollment.is_active is True
