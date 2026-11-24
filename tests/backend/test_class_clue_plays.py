"""Tests for the teacher's classroom clue-play report."""

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.config import get_settings
from app.models.accounts import ClassStaff, User
from app.models.content import Location
from app.models.engagement import Class, ClassEnrollment
from app.models.enums import ClassStaffRole, LocationStatus, SessionState, UserRole
from app.models.game import GameSession, SessionLocation

DATES = {"start_date": "2026-11-14", "end_date": "2026-11-14"}


def test_class_clue_plays_reports_rows_and_pages(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    make_location: Callable[..., Location],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    classroom = make_class()
    owner = make_user(role=UserRole.STAFF)
    make_class_staff(classroom, owner)
    first_student, second_student = make_user(), make_user()
    start = datetime(2026, 11, 14, tzinfo=ZoneInfo(get_settings().app_timezone))
    end = start + timedelta(days=1)
    first_game = make_game_session(student=first_student, class_id=classroom.id, started_at=start)
    second_game = make_game_session(
        student=second_student, class_id=classroom.id, started_at=end - timedelta(seconds=1)
    )
    first_room = make_location()
    second_room = make_location()
    make_session_location(
        first_game, 1, location_id=first_room.id, status=LocationStatus.FAILED, clue_plays=3
    )
    make_session_location(first_game, 2, location_id=second_room.id, status=LocationStatus.PENDING)
    make_session_location(
        second_game, 1, location_id=first_room.id, status=LocationStatus.FAILED, clue_plays=5
    )

    other_class = make_class()
    for class_id, started_at in (
        (classroom.id, start - timedelta(seconds=1)),
        (classroom.id, end),
        (other_class.id, start),
        (None, start),
    ):
        game = make_game_session(
            student=first_student,
            class_id=class_id,
            started_at=started_at,
            state=SessionState.COMPLETED,
            completed_at=started_at,
        )
        make_session_location(game, 1, clue_plays=9)

    path = f"/api/v1/teacher/classes/{classroom.id}/clue-plays"
    headers = auth_headers(owner)
    response = client.get(path, headers=headers, params=DATES)
    assert response.status_code == 200, response.text
    data = response.json()
    assert (data["page"], data["page_size"], data["total"], data["has_next_page"]) == (
        1,
        20,
        3,
        False,
    )
    newest, earlier, unplayed = data["items"]
    assert newest == {
        "student_id": str(second_student.id),
        "username": second_student.username,
        "game_id": str(second_game.id),
        "game_started_at": newest["game_started_at"],
        "location_id": str(first_room.id),
        "clue_plays": 5,
        "result": LocationStatus.FAILED,
    }
    assert datetime.fromisoformat(newest["game_started_at"]) == second_game.started_at
    assert earlier["student_id"] == str(first_student.id)
    assert earlier["game_id"] == str(first_game.id)
    assert earlier["clue_plays"] == 3
    assert unplayed["game_id"] == str(first_game.id)
    assert unplayed["clue_plays"] == 0
    assert unplayed["result"] == LocationStatus.PENDING

    for page, items, has_next in (
        (1, [newest, earlier], True),
        (2, [unplayed], False),
        (3, [], False),
    ):
        response = client.get(path, headers=headers, params={**DATES, "page": page, "page_size": 2})
        assert response.status_code == 200, response.text
        assert response.json() == {
            "items": items,
            "page": page,
            "page_size": 2,
            "total": 3,
            "has_next_page": has_next,
        }


def test_class_clue_plays_rejects_unauthorized_requests_and_invalid_filters(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    make_enrollment: Callable[..., ClassEnrollment],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    classroom = make_class()
    owner = make_user(role=UserRole.STAFF)
    make_class_staff(classroom, owner)
    assistant = make_user(role=UserRole.STUDENT)
    make_class_staff(classroom, assistant, role=ClassStaffRole.ASSISTANT)
    enrolled = make_user(role=UserRole.STUDENT)
    make_enrollment(enrolled, classroom)
    path = f"/api/v1/teacher/classes/{classroom.id}/clue-plays"

    assert client.get(path, params=DATES).status_code == 401
    assert client.get(path, headers=auth_headers(assistant), params=DATES).status_code == 200
    for unauthorized in (enrolled, make_user(), make_user(role=UserRole.STAFF)):
        assert client.get(path, headers=auth_headers(unauthorized), params=DATES).status_code == 403

    headers = auth_headers(owner)
    unknown = f"/api/v1/teacher/classes/{uuid4()}/clue-plays"
    assert client.get(unknown, headers=headers, params=DATES).status_code == 404
    for invalid in ({"page": 0}, {"page_size": 101}, {"start_date": "2026-11-15"}):
        response = client.get(path, headers=headers, params={**DATES, **invalid})
        assert response.status_code == 422, response.text
