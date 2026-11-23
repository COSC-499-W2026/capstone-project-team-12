"""Tests for A16 classroom location analytics."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.engagement.interface import get_class_location_analytics
from app.models.accounts import User
from app.models.content import Location
from app.models.engagement import Class
from app.models.enums import (
    ArrivalOverride,
    ClassStaffRole,
    LocationStatus,
    UserRole,
)


def test_location_statistics_and_time_cap(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_game_session: Callable[..., object],
    make_session_location: Callable[..., object],
    make_location: Callable[..., Location],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, teacher)

    location_id = make_location().id
    started = datetime(2026, 11, 1, 12, tzinfo=UTC)

    game1 = make_game_session(class_id=school_class.id)
    game2 = make_game_session(class_id=school_class.id)

    make_session_location(
        game1,
        3,
        location_id=location_id,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        attempts_used=2,
        started_at=started,
        completed_at=started + timedelta(minutes=40),
        flags=["speed_anomaly"],
        arrival_override=ArrivalOverride.GPS_UNAVAILABLE,
    )
    make_session_location(
        game2,
        1,
        location_id=location_id,
        status=LocationStatus.FAILED,
        attempts_used=3,
        started_at=started,
    )

    results = get_class_location_analytics(school_class.id, db).locations

    assert len(results) == 1
    result = results[0]
    assert result.location_id == location_id
    assert result.finished == 2
    assert result.completed == 1
    assert result.failed == 1
    assert result.success_rate == 50.0
    assert result.photo_attempts == 5
    assert result.average_time_seconds == 1800.0
    assert result.flagged_results == 1
    assert result.gps_overrides == 1


def test_rooms_in_the_same_game_position_have_distinct_rows(
    db: Session,
    make_class: Callable[..., Class],
    make_game_session: Callable[..., object],
    make_session_location: Callable[..., object],
    make_location: Callable[..., Location],
) -> None:
    school_class = make_class()
    rooms = [make_location(), make_location()]
    for room in rooms:
        game = make_game_session(class_id=school_class.id)
        make_session_location(game, 1, location_id=room.id, status=LocationStatus.FAILED)

    rows = get_class_location_analytics(school_class.id, db).locations
    assert [row.location_id for row in rows] == sorted(room.id for room in rooms)


def test_excludes_practice_other_classes_and_unfinished(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_game_session: Callable[..., object],
    make_session_location: Callable[..., object],
    make_location: Callable[..., Location],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class()
    other_class = make_class()
    make_class_staff(school_class, teacher)

    location_id = make_location().id
    completed_at = datetime(2026, 11, 1, 12, tzinfo=UTC)

    classroom_game = make_game_session(class_id=school_class.id)
    practice_game = make_game_session()
    other_game = make_game_session(class_id=other_class.id)

    make_session_location(
        classroom_game,
        1,
        location_id=location_id,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=completed_at,
    )
    make_session_location(
        classroom_game,
        2,
        status=LocationStatus.SKIPPED,
        skip_reason="session_ended",
    )
    make_session_location(
        classroom_game,
        3,
        status=LocationStatus.PENDING,
    )
    make_session_location(
        practice_game,
        1,
        location_id=location_id,
        status=LocationStatus.FAILED,
    )
    make_session_location(
        other_game,
        1,
        location_id=location_id,
        status=LocationStatus.FAILED,
    )

    results = get_class_location_analytics(school_class.id, db).locations

    assert len(results) == 1
    assert results[0].location_id == location_id
    assert results[0].finished == 1
    assert results[0].completed == 1
    assert results[0].success_rate == 100.0


def test_empty_class_returns_no_location_statistics(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, teacher)

    analytics = get_class_location_analytics(school_class.id, db)
    assert analytics.locations == []
    assert analytics.class_stats.finished == 0
    assert analytics.class_stats.average_time_seconds is None


def test_teacher_analytics_endpoint(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_game_session: Callable[..., object],
    make_session_location: Callable[..., object],
    make_location: Callable[..., Location],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, teacher)
    second, fourth = make_location().id, make_location().id

    game = make_game_session(class_id=school_class.id)
    make_session_location(
        game,
        2,
        location_id=second,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        attempts_used=1,
        completed_at=datetime(2026, 11, 1, 12, tzinfo=UTC),
    )

    make_session_location(
        game, 4, location_id=fourth, status=LocationStatus.FAILED, attempts_used=2
    )

    response = client.get(
        f"/api/v1/teacher/classes/{school_class.id}/analytics",
        headers=auth_headers(teacher),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["class_stats"]["finished"] == 2
    assert data["class_stats"]["success_rate"] == 50.0
    assert data["class_stats"]["photo_attempts"] == 3
    assert [item["location_id"] for item in data["locations"]] == sorted(map(str, (second, fourth)))
    assert all(
        {"label", "room_number", "name", "location_name"}.isdisjoint(item)
        for item in data["locations"]
    )


@pytest.mark.parametrize("role", [UserRole.STAFF, UserRole.STUDENT])
def test_unauthorized_user_cannot_view_analytics(
    role: UserRole,
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
) -> None:
    outsider = make_user(role=role)
    school_class = make_class()

    response = client.get(
        f"/api/v1/teacher/classes/{school_class.id}/analytics",
        headers=auth_headers(outsider),
    )

    assert response.status_code == 403


@pytest.mark.parametrize("role", [UserRole.STAFF, UserRole.STUDENT])
def test_assistant_can_view_analytics(
    role: UserRole,
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_game_session: Callable[..., object],
    make_session_location: Callable[..., object],
    make_location: Callable[..., Location],
) -> None:
    assistant = make_user(role=role)
    school_class = make_class()
    make_class_staff(school_class, assistant, role=ClassStaffRole.ASSISTANT)

    location = make_location()
    game = make_game_session(class_id=school_class.id)
    make_session_location(game, 1, location_id=location.id, status=LocationStatus.FAILED)

    response = client.get(
        f"/api/v1/teacher/classes/{school_class.id}/analytics",
        headers=auth_headers(assistant),
    )

    assert response.status_code == 200, response.text
    (row,) = response.json()["locations"]
    assert row["location_id"] == str(location.id)
    assert {"label", "room_number", "name", "location_name"}.isdisjoint(row)


def test_unknown_class_cannot_be_read(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    response = client.get(
        f"/api/v1/teacher/classes/{uuid4()}/analytics",
        headers=auth_headers(teacher),
    )
    assert response.status_code == 404


def test_owner_can_read_archived_class_analytics(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class(archived_at=datetime.now(UTC))
    make_class_staff(school_class, owner)
    response = client.get(
        f"/api/v1/teacher/classes/{school_class.id}/analytics",
        headers=auth_headers(owner),
    )
    assert response.status_code == 200, response.text
