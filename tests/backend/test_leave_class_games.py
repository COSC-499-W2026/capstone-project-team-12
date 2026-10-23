"""Tests for POST /api/v1/me/classes/{class_id}/leave."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.errors import ErrorCode
from app.models.accounts import User
from app.models.engagement import AccessCode, Class, ClassEnrollment
from app.models.enums import (
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


def assert_error(response: Any, status: int, code: ErrorCode) -> None:
    """Check the application's standard error response."""
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code.value


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
        status=LocationStatus.PENDING,
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


def test_leave_rolls_back_when_game_cleanup_fails(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user()
    school_class = make_class()
    enrollment = make_enrollment(student, school_class)
    student.current_class_id = school_class.id
    db.commit()

    with (
        patch(
            "app.api.v1.classes.end_open_sessions",
            side_effect=RuntimeError("Game cleanup failed"),
        ),
        pytest.raises(RuntimeError, match="Game cleanup failed"),
    ):
        leave_as(client, student, str(school_class.id), auth_headers)

    db.rollback()
    db.refresh(enrollment)
    db.refresh(student)

    assert enrollment.is_active is True
    assert enrollment.end_reason is None
    assert enrollment.left_at is None
    assert student.current_class_id == school_class.id


def test_student_can_rejoin_after_leaving(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    make_access_code: Callable[..., AccessCode],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user()
    school_class = make_class()
    enrollment = make_enrollment(student, school_class)
    access_code = make_access_code(school_class)
    enrollment_id = enrollment.id

    leave_response = leave_as(client, student, str(school_class.id), auth_headers)
    assert leave_response.status_code == 204, leave_response.text

    join_response = client.post(
        "/api/v1/me/classes/join",
        json={"access_code": access_code.code},
        headers=auth_headers(student),
    )

    assert join_response.status_code == 200, join_response.text

    db.refresh(enrollment)
    db.refresh(student)

    assert enrollment.id == enrollment_id
    assert enrollment.is_active is True
    assert enrollment.end_reason is None
    assert enrollment.left_at is None
    assert student.current_class_id == school_class.id

    rows = (
        db.query(ClassEnrollment).filter_by(student_id=student.id, class_id=school_class.id).all()
    )
    assert len(rows) == 1
