"""The proposal test cases for privacy requests, end to end through the admin API: exporting a
user's data as a JSON file, and deleting the account, which anonymises it."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts import anonymise as anonymise_module
from app.engagement.interface import get_class_leaderboard
from app.errors import ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.audit import AuditEvent
from app.models.email import EmailOutbox
from app.models.engagement import (
    Achievement,
    Class,
    ClassEnrollment,
    PointsLedger,
    UserAchievement,
    UserLanguageStats,
)
from app.models.enums import (
    AuditAction,
    ClassStaffRole,
    EmailKind,
    Language,
    LocationStatus,
    PointsSourceType,
    UserRole,
)
from app.models.game import GameSession, SessionLocation
from app.services.email import EmailService


@pytest.fixture
def admin(make_user: Callable[..., User]) -> User:
    return make_user(role=UserRole.ADMIN)


@pytest.fixture
def export(
    client: TestClient, auth_headers: Callable[[User], dict[str, str]]
) -> Callable[[User, UUID], Response]:
    """GET /admin/users/{target}/export as `caller`."""
    return lambda caller, target: client.get(
        f"/api/v1/admin/users/{target}/export", headers=auth_headers(caller)
    )


@pytest.fixture
def delete_user(
    client: TestClient, auth_headers: Callable[[User], dict[str, str]]
) -> Callable[[User, UUID, str], Response]:
    """DELETE /admin/users/{target} as `caller`, typing `username` to confirm."""
    return lambda caller, target, username: client.request(
        "DELETE",
        f"/api/v1/admin/users/{target}",
        json={"username": username},
        headers=auth_headers(caller),
    )


def award(db: Session, user: User, school_class: Class, source_ref: str, points: int) -> None:
    db.add(
        PointsLedger(
            user_id=user.id,
            source_type=PointsSourceType.LOCATION,
            source_ref=source_ref,
            class_id=school_class.id,
            language=Language.JA,
            points=points,
        )
    )
    db.flush()


def class_points(db: Session, school_class: Class) -> list[int]:
    return [entry.total_points for entry in get_class_leaderboard(school_class.id, db)]


def test_case_1_the_export_holds_the_data_and_a_deletion_keeps_the_class_points(
    db: Session,
    admin: User,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    make_enrollment: Callable[..., ClassEnrollment],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    export: Callable[[User, UUID], Response],
    delete_user: Callable[[User, UUID, str], Response],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Proposal Test Case 1 (positive)
    japanese, assisted = make_class(), make_class()
    student, classmate = make_user(), make_user()
    make_enrollment(student, japanese)
    make_enrollment(classmate, japanese)
    make_class_staff(assisted, student, role=ClassStaffRole.ASSISTANT)
    game = make_game_session(student, class_id=japanese.id)
    row = make_session_location(
        game, 1, status=LocationStatus.COMPLETED, points_awarded=100, completed_at=datetime.now(UTC)
    )
    award(db, student, japanese, str(row.id), 100)
    award(db, classmate, japanese, "another-location", 50)
    db.add(UserLanguageStats(user_id=student.id, language=Language.JA, total_points=100))
    streak = db.scalar(select(Achievement).where(Achievement.achievement_key == "week_warrior"))
    assert streak is not None
    db.add(UserAchievement(user_id=student.id, achievement_id=streak.id, language=None))
    EmailService.queue_email(student, EmailKind.TA_INVITATION, db)  # not sent yet

    response = export(admin, student.id)

    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == (
        f'attachment; filename="user-{student.id}.json"'
    )
    body = response.json()
    assert body["profile"]["id"] == str(student.id)
    assert [item["class_id"] for item in body["class_enrolments"]] == [str(japanese.id)]
    assert [item["class_id"] for item in body["ta_roles"]] == [str(assisted.id)]
    assert [item["id"] for item in body["sessions"]] == [str(game.id)]
    assert body["points"]["totals"] == [{"language": "ja", "total_points": 100}]
    assert [item["source_ref"] for item in body["points"]["awards"]] == [str(row.id)]
    # once, though the badge list of every language includes a streak badge
    assert [(badge["achievement_key"], badge["language"]) for badge in body["badges"]] == [
        ("week_warrior", None)
    ]
    assert body["photos"] == []  # no photo is stored yet

    assert class_points(db, japanese) == [100, 50]
    deleted_photos: list[UUID] = []
    monkeypatch.setattr(
        anonymise_module, "delete_user_photos", lambda user_id, _: deleted_photos.append(user_id)
    )

    assert delete_user(admin, student.id, student.username).status_code == 204

    db.refresh(student)
    assert (student.email, student.username) == (
        f"deleted-{student.id.hex}@deleted.invalid",
        f"deleted-user-{student.id.hex[:17]}",
    )
    assert deleted_photos == [student.id]
    # the pending TA invitation is cancelled: its email is gone before it was sent
    assert list(db.scalars(select(EmailOutbox.id).where(EmailOutbox.user_id == student.id))) == []
    assert class_points(db, japanese) == [100, 50]


def test_case_2_owners_a_mistyped_username_and_non_admins_are_refused(
    db: Session,
    admin: User,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    delete_user: Callable[[User, UUID, str], Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
) -> None:
    # Proposal Test Case 2 (negative)
    teacher, new_owner = make_user(role=UserRole.STAFF), make_user(role=UserRole.STAFF)
    transferred, archived = make_class(), make_class()
    owner_row = make_class_staff(transferred, teacher)
    make_class_staff(archived, teacher)

    refused = delete_user(admin, teacher.id, teacher.username)

    assert_error(refused, 409, ErrorCode.OWNS_ACTIVE_CLASS)
    owned = {item["class_id"] for item in refused.json()["error"]["details"]}
    assert owned == {str(transferred.id), str(archived.id)}

    owner_row.user_id = new_owner.id  # transferred: there is no transfer route yet
    db.flush()
    refused = delete_user(admin, teacher.id, teacher.username)
    assert_error(refused, 409, ErrorCode.OWNS_ACTIVE_CLASS)
    assert refused.json()["error"]["details"] == [{"class_id": str(archived.id)}]

    archived.archived_at = datetime.now(UTC)
    db.flush()
    mistyped = delete_user(admin, teacher.id, teacher.username.upper())
    assert_error(mistyped, 422, ErrorCode.USERNAME_MISMATCH)
    assert_error(
        delete_user(new_owner, teacher.id, teacher.username), 403, ErrorCode.FORBIDDEN_ROLE
    )
    db.refresh(teacher)
    assert teacher.is_active is True

    assert delete_user(admin, teacher.id, teacher.username).status_code == 204
    # refused deletions record nothing
    entries = select(AuditEvent.action).where(AuditEvent.target_user_id == teacher.id)
    assert list(db.scalars(entries)) == [AuditAction.DELETE_USER]
