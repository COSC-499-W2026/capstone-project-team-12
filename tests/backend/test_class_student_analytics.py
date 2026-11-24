"""Tests for A16 individual classroom student statistics."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.engagement.interface import get_class_student_analytics
from app.models.accounts import User
from app.models.engagement import Class, ClassEnrollment, PointsLedger, UserStats
from app.models.enums import ClassStaffRole, LocationStatus, SessionState, UserRole


def test_student_statistics_exclude_practice_and_other_classes(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    make_game_session: Callable[..., object],
    make_session_location: Callable[..., object],
    freeze_clock: Callable[..., None],
) -> None:
    now = datetime(2026, 7, 10, 20, tzinfo=UTC)
    freeze_clock(now, "app.engagement.streaks")
    student = make_user()
    school_class = make_class()
    other_class = make_class()

    make_enrollment(student, school_class)
    stats = UserStats(
        user_id=student.id,
        current_streak_days=7,
        best_streak_days=7,
        last_active_date=now.date() - timedelta(days=1),
    )
    db.add(stats)

    game = make_game_session(
        student=student,
        class_id=school_class.id,
        total_score=100,
    )
    practice = make_game_session(
        student=student,
        state=SessionState.COMPLETED,
        completed_at=now - timedelta(days=1),
        total_score=100,
    )
    other = make_game_session(
        student=student,
        class_id=other_class.id,
        state=SessionState.COMPLETED,
        completed_at=now - timedelta(days=2),
        total_score=100,
    )

    for source_type, points, class_id in (
        ("location", 100, school_class.id),
        ("daily_challenge", 50, school_class.id),
        ("streak_milestone", 100, school_class.id),
        ("location", 100, other_class.id),
        ("location", 100, None),
    ):
        db.add(
            PointsLedger(
                user_id=student.id,
                source_type=source_type,
                source_ref=str(class_id),
                class_id=class_id,
                language=school_class.target_language,
                points=points,
            )
        )
    db.flush()

    make_session_location(
        game,
        1,
        status=LocationStatus.COMPLETED,
        clue_plays=4,
        points_awarded=100,
        completed_at=now,
        flags=["similar_photo"],
    )
    make_session_location(
        game,
        2,
        status=LocationStatus.FAILED,
    )
    make_session_location(
        game,
        3,
        status=LocationStatus.PENDING,
        clue_plays=2,
        flags=["speed_anomaly"],
    )
    make_session_location(
        practice,
        1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        clue_plays=10,
        completed_at=now - timedelta(days=1),
        flags=["speed_anomaly"],
    )
    make_session_location(
        other,
        1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        clue_plays=10,
        completed_at=now - timedelta(days=2),
    )

    results = get_class_student_analytics(school_class.id, db)

    assert len(results) == 1
    result = results[0]

    assert result.student_id == student.id
    assert result.username == student.username
    assert result.is_active_enrollment is True
    assert result.sessions == 1
    assert result.completed_locations == 1
    assert result.failed_locations == 1
    assert result.classroom_points == 250
    assert result.total_clue_plays == 6
    assert result.streak_days == 7
    assert result.flags == ["similar_photo"]
    assert result.success_rate == 50.0
    assert result.last_activity_at is not None

    freeze_clock(now + timedelta(days=1), "app.engagement.streaks")
    assert get_class_student_analytics(school_class.id, db)[0].streak_days == 0
    assert stats.current_streak_days == 7


def test_students_without_games_remain_visible(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    student = make_user()
    school_class = make_class()

    make_class_staff(school_class, teacher)
    make_enrollment(student, school_class)

    results = get_class_student_analytics(school_class.id, db)

    assert len(results) == 1

    result = results[0]
    assert result.student_id == student.id
    assert result.is_active_enrollment is True
    assert result.sessions == 0
    assert result.completed_locations == 0
    assert result.failed_locations == 0
    assert result.classroom_points == 0
    assert result.total_clue_plays == 0
    assert result.success_rate == 0.0
    assert result.last_activity_at is None


def test_empty_class_has_no_student_statistics(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class()

    make_class_staff(school_class, teacher)

    assert get_class_student_analytics(school_class.id, db) == []


def test_student_statistics_endpoint(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    student = make_user()
    school_class = make_class()

    make_class_staff(school_class, teacher)
    make_enrollment(student, school_class)

    response = client.get(
        f"/api/v1/teacher/classes/{school_class.id}/students",
        headers=auth_headers(teacher),
    )

    assert response.status_code == 200, response.text

    data = response.json()

    assert len(data) == 1
    assert data[0]["student_id"] == str(student.id)
    assert data[0]["username"] == student.username
    assert data[0]["is_active_enrollment"] is True
    assert data[0]["sessions"] == 0
    assert data[0]["completed_locations"] == 0
    assert data[0]["failed_locations"] == 0
    assert data[0]["classroom_points"] == 0
    assert data[0]["streak_days"] == 0
    assert data[0]["flags"] == []
    assert data[0]["success_rate"] == 0.0
    assert data[0]["last_activity_at"] is None


def test_unauthorized_teacher_cannot_view_students(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
) -> None:
    outsider = make_user(role=UserRole.STAFF)
    school_class = make_class()

    response = client.get(
        f"/api/v1/teacher/classes/{school_class.id}/students",
        headers=auth_headers(outsider),
    )

    assert response.status_code == 403


def test_assistant_can_view_student_statistics(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    assistant = make_user()
    school_class = make_class()

    make_class_staff(
        school_class,
        assistant,
        role=ClassStaffRole.ASSISTANT,
    )

    response = client.get(
        f"/api/v1/teacher/classes/{school_class.id}/students",
        headers=auth_headers(assistant),
    )

    assert response.status_code == 200, response.text
    assert response.json() == []


def test_student_statistics_do_not_include_unenrolled_students(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_game_session: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    outsider_student = make_user()
    school_class = make_class()

    make_class_staff(school_class, teacher)

    make_game_session(
        student=outsider_student,
        class_id=school_class.id,
    )

    assert get_class_student_analytics(school_class.id, db) == []


def test_former_student_remains_visible_in_archived_class(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_enrollment: Callable[..., ClassEnrollment],
    end_enrolment: Callable[..., None],
) -> None:
    """Former students remain in historical statistics after archiving."""
    teacher = make_user(role=UserRole.STAFF)
    student = make_user()
    school_class = make_class()

    make_class_staff(school_class, teacher)

    enrollment = make_enrollment(student, school_class)
    end_enrolment(enrollment)

    school_class.archived_at = datetime.now(UTC)
    db.flush()

    results = get_class_student_analytics(school_class.id, db)

    assert len(results) == 1

    result = results[0]
    assert result.student_id == student.id
    assert result.is_active_enrollment is False
    assert result.left_at is not None
    assert result.sessions == 0
    assert result.classroom_points == 0


def test_unknown_class_returns_404(client, auth_headers, make_user) -> None:
    teacher = make_user(role=UserRole.STAFF)
    response = client.get(
        f"/api/v1/teacher/classes/{uuid4()}/students", headers=auth_headers(teacher)
    )
    assert response.status_code == 404
