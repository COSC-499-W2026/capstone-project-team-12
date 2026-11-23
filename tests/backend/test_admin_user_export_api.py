"""GET /api/v1/admin/users/{id}/export: an admin downloads what is stored about an account as a
JSON file, and every attempt is written to the audit log."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.export import export_user
from app.errors import AppError, ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.audit import AuditEvent
from app.models.engagement import Class, ClassEnrollment
from app.models.enums import AuditAction, AuditResult, ClassStaffRole, Language, UserRole


@pytest.fixture
def admin(make_user: Callable[..., User]) -> User:
    return make_user(role=UserRole.ADMIN)


@pytest.fixture
def export(
    client: TestClient, auth_headers: Callable[[User], dict[str, str]]
) -> Callable[[User, UUID], Response]:
    """GET /admin/users/{target}/export as `caller`."""

    def get(caller: User, target: UUID) -> Response:
        return client.get(f"/api/v1/admin/users/{target}/export", headers=auth_headers(caller))

    return get


def audit_entries(db: Session, actor: User) -> list[AuditEvent]:
    entries = select(AuditEvent).where(AuditEvent.actor_id == actor.id)
    return list(db.scalars(entries.order_by(AuditEvent.created_at)))


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
    assert (entry.action, entry.result, entry.target_id, entry.ip_address) == (
        AuditAction.EXPORT_USER,
        AuditResult.SUCCESS,
        student.id,
        "testclient",
    )
    assert entry.details == {
        "email": student.email,
        "username": student.username,
        "role": "student",
    }
    assert datetime.fromisoformat(body["exported_at"]) == entry.created_at


def test_a_teachers_export_lists_the_classes_they_assist_but_not_the_ones_they_own(
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
    assert body["class_enrolments"] == []


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


def test_an_unknown_id_is_user_not_found_and_audited_as_a_failure(
    db: Session,
    admin: User,
    export: Callable[[User, UUID], Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
) -> None:
    unknown = uuid4()

    assert_error(export(admin, unknown), 404, ErrorCode.USER_NOT_FOUND)

    [entry] = audit_entries(db, admin)
    assert (entry.action, entry.result, entry.target_id, entry.details) == (
        AuditAction.EXPORT_USER,
        AuditResult.FAILURE,
        unknown,
        {"error": "USER_NOT_FOUND"},
    )


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"role": UserRole.STUDENT}, ErrorCode.FORBIDDEN_ROLE),
        ({"role": UserRole.STAFF}, ErrorCode.FORBIDDEN_ROLE),
        ({"role": UserRole.STAFF, "can_manage_content": True}, ErrorCode.FORBIDDEN_ROLE),
        ({"role": UserRole.ADMIN, "is_active": False}, ErrorCode.ACCOUNT_DISABLED),
    ],
    ids=["student", "teacher", "content-manager", "deactivated-admin"],
)
def test_only_active_admins_can_export(
    db: Session,
    make_user: Callable[..., User],
    export: Callable[[User, UUID], Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
    fields: dict[str, Any],
    code: ErrorCode,
) -> None:
    caller = make_user(**fields)

    assert_error(export(caller, caller.id), 403, code)  # even their own account

    assert audit_entries(db, caller) == []


def test_without_a_token_the_request_is_401(
    client: TestClient,
    make_user: Callable[..., User],
    assert_error: Callable[[Response, int, ErrorCode], None],
) -> None:
    response = client.get(f"/api/v1/admin/users/{make_user().id}/export")
    assert_error(response, 401, ErrorCode.NOT_AUTHENTICATED)


def test_the_service_does_not_commit(
    db: Session, admin: User, make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    commits: list[None] = []
    monkeypatch.setattr(db, "commit", lambda: commits.append(None))

    export_user(admin.id, make_user().id, db)
    with pytest.raises(AppError):
        export_user(admin.id, uuid4(), db)

    assert commits == []
