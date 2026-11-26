"""POST /api/v1/admin/staff: an admin creates a teacher account, its set-password email is
queued and the new account is recorded in the admin audit log."""

from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts.interface import create_teacher
from app.errors import ErrorCode
from app.models.accounts import User
from app.models.audit import AuditEvent
from app.models.email import EmailOutbox
from app.models.enums import AuditAction, EmailKind, EmailStatus, UserRole

URL = "/api/v1/admin/staff"


@pytest.fixture
def admin(make_user: Callable[..., User]) -> User:
    return make_user(role=UserRole.ADMIN)


@pytest.fixture
def create_as(
    client: TestClient, auth_headers: Callable[[User], dict[str, str]]
) -> Callable[..., Response]:
    """POST a new teacher as `user`; `fields` override a valid body with unique values."""

    def send(user: User, **fields: Any) -> Response:
        return client.post(URL, json=new_teacher() | fields, headers=auth_headers(user))

    return send


def new_teacher() -> dict[str, str]:
    """An address and a username no account has."""
    return {"email": f"{uuid4().hex[:12]}@example.com", "username": f"t_{uuid4().hex[:8]}"}


def audit_entries(db: Session, actor: User) -> list[AuditEvent]:
    return list(db.scalars(select(AuditEvent).where(AuditEvent.actor_id == actor.id)))


def user_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User)) or 0


@pytest.mark.parametrize("can_manage_content", [False, True], ids=["teacher", "content-manager"])
def test_admin_creates_a_teacher_and_queues_a_set_password_email(
    db: Session, admin: User, create_as: Callable[..., Response], can_manage_content: bool
) -> None:
    address = f"{uuid4().hex[:12]}@example.com"
    username = f"Sensei_{uuid4().hex[:6]}"

    response = create_as(
        admin,
        email=f"  {address.upper()} ",
        username=username,
        can_manage_content=can_manage_content,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    teacher = db.get_one(User, UUID(body["id"]))
    assert body["email"] == address  # stored and returned lowercase
    assert {key: body[key] for key in ("username", "role", "can_manage_content", "is_active")} == {
        "username": username,
        "role": UserRole.STAFF,
        "can_manage_content": can_manage_content,
        "is_active": True,
    }
    assert body["created_at"].endswith("Z")
    assert (teacher.email, teacher.role, teacher.can_manage_content) == (
        address,
        UserRole.STAFF,
        can_manage_content,
    )
    # no password until the teacher uses the link, and the address isn't verified yet
    assert teacher.password_hash is None and teacher.email_verified_at is None

    [mail] = db.scalars(select(EmailOutbox).where(EmailOutbox.user_id == teacher.id))
    assert (mail.template, mail.status, mail.to_email) == (
        EmailKind.SET_PASSWORD,
        EmailStatus.PENDING,
        address,
    )
    assert mail.params == {}  # the link token is only made when the mail is sent

    [entry] = audit_entries(db, admin)
    assert (entry.action, entry.target_user_id, entry.target_class_id) == (
        AuditAction.CREATE_STAFF,
        teacher.id,
        None,
    )
    assert entry.details == {"can_manage_content": can_manage_content}


def test_an_email_in_use_is_409_and_nothing_is_written(
    db: Session,
    admin: User,
    make_user: Callable[..., User],
    create_as: Callable[..., Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
) -> None:
    existing = make_user()
    users_before = user_count(db)

    response = create_as(admin, email=existing.email)

    assert_error(response, 409, ErrorCode.EMAIL_TAKEN)
    assert user_count(db) == users_before
    assert db.scalar(select(EmailOutbox).where(EmailOutbox.user_id == existing.id)) is None
    assert audit_entries(db, admin) == []


def test_create_teacher_does_not_commit(
    db: Session, admin: User, transaction_calls: list[str]
) -> None:
    create_teacher(admin.id, **new_teacher(), can_manage_content=False, db=db)

    assert "commit" not in transaction_calls
    assert "rollback" not in transaction_calls


@pytest.mark.parametrize("role", [UserRole.STUDENT, UserRole.STAFF])
def test_non_admins_are_403_and_nothing_is_created(
    db: Session,
    make_user: Callable[..., User],
    create_as: Callable[..., Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
    role: UserRole,
) -> None:
    """test_role_access.py covers require_admin itself; this only checks that the route uses it."""
    caller = make_user(role=role)
    users_before = user_count(db)

    response = create_as(caller)

    assert_error(response, 403, ErrorCode.FORBIDDEN_ROLE)
    assert user_count(db) == users_before
    assert audit_entries(db, caller) == []


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"email": "not-an-email"}, "email"),
        ({"can_manage_content": "maybe"}, "can_manage_content"),
        ({"role": UserRole.ADMIN}, "role"),  # this route only makes teachers
    ],
)
def test_invalid_fields_are_422_and_not_audited(
    db: Session,
    admin: User,
    create_as: Callable[..., Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
    fields: dict[str, Any],
    field: str,
) -> None:
    response = create_as(admin, **fields)

    assert_error(response, 422, ErrorCode.VALIDATION_ERROR)
    assert field in [detail["field"] for detail in response.json()["error"]["details"]]
    assert audit_entries(db, admin) == []
