"""Tests for A16 classroom participation CSV export."""

import csv
from collections.abc import Callable
from datetime import UTC, datetime
from io import StringIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.content import Location
from app.models.engagement import Achievement, Class, ClassEnrollment, PointsLedger, UserAchievement
from app.models.enums import (
    AchievementCategory,
    Difficulty,
    EnrollmentEndReason,
    LocationStatus,
    SessionState,
    UserRole,
)


def _create_completed_location(
    make_game_session: Callable,
    make_session_location: Callable,
    student: User,
    classroom: Class | None,
    *,
    location_id=None,
    difficulty: Difficulty = Difficulty.BASIC,
    completed_at: datetime | None = None,
):
    """Create a completed classroom or Practice location."""
    timestamp = completed_at or datetime(2026, 11, 14, 12, tzinfo=UTC)

    game = make_game_session(
        student=student,
        class_id=classroom.id if classroom is not None else None,
        difficulty=difficulty,
        state=SessionState.COMPLETED,
        started_at=timestamp,
        completed_at=timestamp,
    )

    location = make_session_location(
        game,
        1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=timestamp,
    )

    if location_id is not None:
        location.location_id = location_id

    return location


def test_export_contains_correct_participation_marks(
    db: Session,
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_enrollment: Callable[..., ClassEnrollment],
    end_enrolment: Callable[..., None],
    make_game_session: Callable,
    make_session_location: Callable,
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    student = make_user()
    inactive_student = make_user()
    classroom = make_class()

    make_class_staff(classroom, teacher)

    make_enrollment(student, classroom)
    idle_student = make_user()
    make_enrollment(idle_student, classroom)

    inactive_enrollment = make_enrollment(inactive_student, classroom)
    end_enrolment(inactive_enrollment)

    for _ in range(5):
        _create_completed_location(make_game_session, make_session_location, student, classroom)
    timestamp = datetime(2026, 11, 14, 12, tzinfo=UTC)
    achievement = db.scalar(
        select(Achievement).where(Achievement.category == AchievementCategory.LEARNING)
    )
    assert achievement is not None
    for user, class_id, points in (
        (student, classroom.id, 100),
        (student, None, 500),
        (inactive_student, classroom.id, 50),
    ):
        db.add(
            PointsLedger(
                user_id=user.id,
                class_id=class_id,
                language=classroom.target_language,
                source_type="location",
                source_ref=str(uuid4()),
                points=points,
                created_at=timestamp,
            )
        )
    db.add(
        UserAchievement(
            user_id=student.id,
            achievement_id=achievement.id,
            language=classroom.target_language,
            unlocked_at=timestamp,
        )
    )
    db.flush()

    response = client.get(
        f"/api/v1/teacher/classes/{classroom.id}/participation.csv",
        params={"start_date": "2026-11-14", "end_date": "2026-11-14"},
        headers=auth_headers(teacher),
    )

    assert response.status_code == 200, response.text
    assert "text/csv" in response.headers["content-type"]
    assert "attachment;" in response.headers["content-disposition"]

    rows = list(csv.DictReader(StringIO(response.text)))

    assert len(rows) == 3
    idle_row = next(row for row in rows if row["student_id"] == str(idle_student.id))
    assert idle_row["is_active_enrollment"] == "True"
    assert idle_row["participation_percent"] == "0"

    student_row = next(row for row in rows if row["student_id"] == str(student.id))
    inactive_row = next(row for row in rows if row["username"] == inactive_student.username)

    assert student_row["username"] == student.username
    assert student_row["enrollment_status"] == "active"
    assert student_row["is_active_enrollment"] == "True"
    assert student_row["distinct_completed_locations"] == "5"
    assert student_row["target_locations"] == "10"
    assert student_row["participation_percent"] == "50"

    assert [student_row[key] for key in ("sessions", "active_days", "points", "badges")] == [
        "5",
        "1",
        "100",
        "1",
    ]
    assert inactive_row["points"] == "50"
    assert inactive_row["enrollment_status"] == "left"
    assert inactive_row["distinct_completed_locations"] == "0"
    assert inactive_row["participation_percent"] == "0"


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t"])
def test_export_escapes_formula_like_usernames(
    prefix: str,
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    classroom = make_class()
    make_class_staff(classroom, teacher)
    student = make_user(username=f"{prefix}student")
    make_enrollment(student, classroom)

    response = client.get(
        f"/api/v1/teacher/classes/{classroom.id}/participation.csv",
        params={"start_date": "2026-11-14", "end_date": "2026-11-14"},
        headers=auth_headers(teacher),
    )

    assert response.status_code == 200, response.text
    assert next(csv.DictReader(StringIO(response.text)))["username"] == f"'{student.username}"


def test_export_preserves_enrollment_status_after_archiving(
    db: Session,
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_enrollment: Callable[..., ClassEnrollment],
    end_enrolment: Callable[..., None],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    classroom = make_class()
    make_class_staff(classroom, teacher)

    expected = {}
    for reason in (
        EnrollmentEndReason.LEFT,
        EnrollmentEndReason.REMOVED,
        EnrollmentEndReason.ARCHIVED,
    ):
        student = make_user()
        end_enrolment(make_enrollment(student, classroom), reason)
        expected[str(student.id)] = reason.value
    classroom.archived_at = datetime.now(UTC)
    db.flush()

    response = client.get(
        f"/api/v1/teacher/classes/{classroom.id}/participation.csv",
        params={"start_date": "2026-11-14", "end_date": "2026-11-14"},
        headers=auth_headers(teacher),
    )

    assert response.status_code == 200, response.text
    rows = csv.DictReader(StringIO(response.text))
    assert {row["student_id"]: row["enrollment_status"] for row in rows} == expected


def test_export_does_not_count_repeated_pairs_or_practice(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_enrollment: Callable[..., ClassEnrollment],
    make_game_session: Callable,
    make_session_location: Callable,
    make_location: Callable[..., Location],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    student = make_user()
    classroom = make_class()

    make_class_staff(classroom, teacher)
    make_enrollment(student, classroom)

    shared_location = make_location().id

    # Same location and difficulty repeated: counts once.
    for _ in range(2):
        _create_completed_location(
            make_game_session,
            make_session_location,
            student,
            classroom,
            location_id=shared_location,
            difficulty=Difficulty.BASIC,
        )

    # Practice must not contribute.
    _create_completed_location(
        make_game_session,
        make_session_location,
        student,
        None,
        difficulty=Difficulty.CHALLENGING,
    )

    response = client.get(
        f"/api/v1/teacher/classes/{classroom.id}/participation.csv",
        params={"start_date": "2026-11-14", "end_date": "2026-11-14"},
        headers=auth_headers(teacher),
    )

    assert response.status_code == 200, response.text

    rows = list(csv.DictReader(StringIO(response.text)))

    assert len(rows) == 1
    assert rows[0]["distinct_completed_locations"] == "1"
    assert rows[0]["participation_percent"] == "10"


def test_export_excludes_completions_outside_date_range(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_enrollment: Callable[..., ClassEnrollment],
    make_game_session: Callable,
    make_session_location: Callable,
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    student = make_user()
    classroom = make_class()

    make_class_staff(classroom, teacher)
    make_enrollment(student, classroom)

    for day in (13, 14, 15):
        _create_completed_location(
            make_game_session,
            make_session_location,
            student,
            classroom,
            completed_at=datetime(2026, 11, day, 12, tzinfo=UTC),
        )

    response = client.get(
        f"/api/v1/teacher/classes/{classroom.id}/participation.csv",
        params={"start_date": "2026-11-14", "end_date": "2026-11-14"},
        headers=auth_headers(teacher),
    )

    assert response.status_code == 200, response.text

    rows = list(csv.DictReader(StringIO(response.text)))

    assert len(rows) == 1
    assert rows[0]["distinct_completed_locations"] == "1"
    assert rows[0]["participation_percent"] == "10"


def test_export_respects_custom_target_and_caps_at_100(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_enrollment: Callable[..., ClassEnrollment],
    make_game_session: Callable,
    make_session_location: Callable,
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    student = make_user()
    classroom = make_class()

    make_class_staff(classroom, teacher)
    make_enrollment(student, classroom)

    for _ in range(3):
        _create_completed_location(make_game_session, make_session_location, student, classroom)

    response = client.get(
        f"/api/v1/teacher/classes/{classroom.id}/participation.csv",
        params={
            "start_date": "2026-11-14",
            "end_date": "2026-11-14",
            "target_locations": 2,
        },
        headers=auth_headers(teacher),
    )

    assert response.status_code == 200, response.text

    rows = list(csv.DictReader(StringIO(response.text)))

    assert rows[0]["distinct_completed_locations"] == "3"
    assert rows[0]["target_locations"] == "2"
    assert rows[0]["participation_percent"] == "100"


@pytest.mark.parametrize(("missing_class", "status"), [(False, 403), (True, 404)])
def test_export_requires_class_access(
    missing_class: bool,
    status: int,
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
) -> None:
    outsider = make_user(role=UserRole.STAFF)
    class_id = uuid4() if missing_class else make_class().id

    response = client.get(
        f"/api/v1/teacher/classes/{class_id}/participation.csv",
        params={"start_date": "2026-11-14", "end_date": "2026-11-14"},
        headers=auth_headers(outsider),
    )

    assert response.status_code == status


def test_export_rejects_invalid_date_range(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    classroom = make_class()
    make_class_staff(classroom, teacher)

    response = client.get(
        f"/api/v1/teacher/classes/{classroom.id}/participation.csv",
        params={
            "start_date": "2026-11-15",
            "end_date": "2026-11-14",
        },
        headers=auth_headers(teacher),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["message"] == "start_date must not be after end_date"


def test_export_requires_date_parameters(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    classroom = make_class()
    make_class_staff(classroom, teacher)

    response = client.get(
        f"/api/v1/teacher/classes/{classroom.id}/participation.csv",
        headers=auth_headers(teacher),
    )

    assert response.status_code == 422
