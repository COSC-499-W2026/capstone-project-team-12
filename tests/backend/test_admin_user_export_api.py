"""GET /api/v1/admin/users/{id}/export: an admin downloads what is stored about an account as a
JSON file, and each export is recorded in the admin audit log."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from conftest import audit_entries
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy.orm import Session

from app.accounts.interface import export_user
from app.errors import ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import Class, ClassEnrollment, PointsLedger, UserLanguageStats
from app.models.enums import (
    AuditAction,
    ClassStaffRole,
    Language,
    LocationStatus,
    PointsSourceType,
    SkipReason,
    UserRole,
)
from app.models.game import GameSession, SessionLocation


@pytest.fixture
def export(
    client: TestClient, auth_headers: Callable[[User], dict[str, str]]
) -> Callable[[User, UUID], Response]:
    """GET /admin/users/{target}/export as `caller`."""

    def get(caller: User, target: UUID) -> Response:
        return client.get(f"/api/v1/admin/users/{target}/export", headers=auth_headers(caller))

    return get


def played_game(
    student: User,
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> tuple[GameSession, SessionLocation]:
    """A Practice game whose first location was shuffled away, then completed; the second is
    pending. Returns the game and the completed location."""
    game = make_game_session(student, total_score=100)
    completed = make_session_location(
        game,
        1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
        last_lat=49.9394,
        last_lng=-119.3948,
        last_accuracy_m=8.0,
    )
    # added after its replacement, so only the ordering can put it first
    make_session_location(game, 1, status=LocationStatus.SKIPPED, skip_reason=SkipReason.SHUFFLED)
    make_session_location(game, 2)
    return game, completed


def award_points(db: Session, student: User, source_ref: str) -> None:
    db.add_all(
        [
            PointsLedger(
                user_id=student.id,
                source_type=PointsSourceType.LOCATION,
                source_ref=source_ref,
                language=Language.JA,
                points=100,
            ),
            UserLanguageStats(user_id=student.id, language=Language.JA, total_points=100),
        ]
    )
    db.flush()


def test_a_student_export_holds_their_data_and_no_one_elses(
    db: Session,
    admin: User,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    make_enrollment: Callable[..., ClassEnrollment],
    end_enrolment: Callable[..., None],
    export: Callable[[User, UUID], Response],
) -> None:
    student = make_user(preferred_language=Language.JA, password_hash="never-exported")
    other = make_user()
    japanese = make_class(name="JAPN 101", target_language=Language.JA)
    french = make_class(name="FREN 101", target_language=Language.FR)
    assisted = make_class(name="JAPN 201", target_language=Language.JA)
    joined = datetime.now(UTC) - timedelta(days=30)
    end_enrolment(make_enrollment(student, japanese, joined_at=joined))
    make_enrollment(student, french, joined_at=joined + timedelta(days=1))
    make_class_staff(assisted, student, role=ClassStaffRole.ASSISTANT)
    # another student's rows must stay out
    make_enrollment(other, french)
    make_class_staff(assisted, other, role=ClassStaffRole.ASSISTANT)

    response = export(admin, student.id)

    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == (
        f'attachment; filename="user-{student.id}.json"'
    )
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    profile = body["profile"]
    assert "password_hash" not in profile
    assert (profile["id"], profile["email"], profile["role"], profile["preferred_language"]) == (
        str(student.id),
        student.email,
        "student",
        "ja",
    )
    assert [
        (row["class_id"], row["class_name"], row["is_active"], row["end_reason"])
        for row in body["class_enrolments"]
    ] == [(str(japanese.id), "JAPN 101", False, "left"), (str(french.id), "FREN 101", True, None)]
    assert [(row["class_id"], row["class_name"]) for row in body["ta_roles"]] == [
        (str(assisted.id), "JAPN 201")
    ]

    [entry] = audit_entries(db, admin)
    assert (entry.action, entry.target_user_id, entry.details) == (
        AuditAction.EXPORT_USER,
        student.id,
        {"role": "student"},
    )
    assert datetime.fromisoformat(body["exported_at"]) == entry.created_at


def test_a_students_games_and_points_are_exported_but_not_another_students(
    db: Session,
    admin: User,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    export: Callable[[User, UUID], Response],
) -> None:
    student, other = make_user(), make_user()
    game, completed = played_game(student, make_game_session, make_session_location)
    award_points(db, student, str(completed.id))
    other_game, other_completed = played_game(other, make_game_session, make_session_location)
    award_points(db, other, str(other_completed.id))

    response = export(admin, student.id)

    assert response.status_code == 200, response.text
    body = response.json()
    [session] = body["sessions"]
    assert (session["id"], session["class_id"], session["total_score"]) == (str(game.id), None, 100)
    locations = session["locations"]
    assert [(row["position"], row["status"], row["skip_reason"]) for row in locations] == [
        (1, "skipped", "shuffled"),
        (1, "completed", None),
        (2, "pending", None),
    ]
    assert (locations[1]["last_lat"], locations[1]["last_lng"]) == (49.9394, -119.3948)
    # the content snapshot is campus content, not the student's data
    assert not any(key.endswith("_snapshot") for key in locations[1])
    assert body["points"]["totals"] == [{"language": "ja", "total_points": 100}]
    assert [
        (award["source_type"], award["source_ref"], award["points"])
        for award in body["points"]["awards"]
    ] == [("location", str(completed.id), 100)]
    assert body["photos"] == []  # photos aren't stored yet
    assert str(other_game.id) not in response.text


def test_a_teachers_export_lists_the_classes_they_own_and_the_ones_they_assist(
    admin: User,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    export: Callable[[User, UUID], Response],
) -> None:
    teacher = make_user(role=UserRole.STAFF, can_manage_content=True)
    owned, assisted = make_class(), make_class()
    make_class_staff(owned, teacher)
    make_class_staff(assisted, teacher, role=ClassStaffRole.ASSISTANT)

    response = export(admin, teacher.id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["profile"]["role"], body["profile"]["can_manage_content"]) == ("staff", True)
    assert [row["class_id"] for row in body["ta_roles"]] == [str(assisted.id)]
    assert [row["class_id"] for row in body["owned_classes"]] == [str(owned.id)]
    assert (body["class_enrolments"], body["sessions"], body["photos"]) == ([], [], [])
    assert body["points"] == {"totals": [], "awards": []}


@pytest.mark.parametrize("role", list(UserRole))
def test_any_account_can_be_exported_deactivated_ones_too(
    admin: User,
    make_user: Callable[..., User],
    export: Callable[[User, UUID], Response],
    role: UserRole,
) -> None:
    target = make_user(role=role, is_active=False)

    response = export(admin, target.id)

    assert response.status_code == 200, response.text
    assert (response.json()["profile"]["role"], response.json()["profile"]["is_active"]) == (
        role.value,
        False,
    )


def test_an_unknown_id_is_user_not_found_and_nothing_is_recorded(
    db: Session,
    admin: User,
    export: Callable[[User, UUID], Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
) -> None:
    assert_error(export(admin, uuid4()), 404, ErrorCode.USER_NOT_FOUND)

    assert audit_entries(db, admin) == []


def test_export_user_does_not_commit(
    db: Session, admin: User, make_user: Callable[..., User], transaction_calls: list[str]
) -> None:
    export_user(admin.id, make_user().id, db)

    assert "commit" not in transaction_calls
