"""POST /api/v1/admin/staff: an admin creates a teacher account, its set-password email is
queued and the attempt is written to the audit log (A27)."""

from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts import staff
from app.accounts.staff import create_teacher
from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.audit import AuditEvent
from app.models.email import EmailOutbox
from app.models.enums import AuditAction, AuditResult, EmailKind, EmailStatus, UserRole

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
        body = {"email": f"{uuid4().hex[:12]}@example.com", "username": f"t_{uuid4().hex[:8]}"}
        return client.post(URL, json=body | fields, headers=auth_headers(user))

    return send


def audit_entries(db: Session, actor: User) -> list[AuditEvent]:
    return list(db.scalars(select(AuditEvent).where(AuditEvent.actor_id == actor.id)))


def users_with_email(db: Session, email: str) -> int:
    return db.scalar(select(func.count()).select_from(User).where(User.email == email)) or 0


def test_admin_creates_a_teacher_and_queues_a_set_password_email(
    db: Session, admin: User, create_as: Callable[..., Response]
) -> None:
    address = f"{uuid4().hex[:12]}@example.com"
    username = f"Sensei_{uuid4().hex[:6]}"

    response = create_as(admin, email=f"  {address.upper()} ", username=username)

    assert response.status_code == 201, response.text
    body = response.json()
    teacher = db.get_one(User, UUID(body["id"]))
    assert body["email"] == address  # stored and returned lowercase
    assert {key: body[key] for key in ("username", "role", "can_manage_content", "is_active")} == {
        "username": username,
        "role": UserRole.STAFF,
        "can_manage_content": False,
        "is_active": True,
    }
    assert body["created_at"].endswith("Z")
    assert (teacher.email, teacher.role, teacher.can_manage_content) == (
        address,
        UserRole.STAFF,
        False,
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
    assert (entry.action, entry.result, entry.target_id) == (
        AuditAction.CREATE_USER,
        AuditResult.SUCCESS,
        teacher.id,
    )
    assert entry.details == {
        "email": address,
        "username": username,
        "role": "staff",
        "can_manage_content": False,
    }
    assert entry.ip_address == "testclient"  # TestClient's client host


def test_admin_can_give_content_rights_when_creating(
    db: Session, admin: User, create_as: Callable[..., Response]
) -> None:
    response = create_as(admin, can_manage_content=True)

    assert response.status_code == 201, response.text
    assert response.json()["can_manage_content"] is True
    teacher = db.get_one(User, UUID(response.json()["id"]))
    assert teacher.can_manage_content is True
    [entry] = audit_entries(db, admin)
    assert entry.details["can_manage_content"] is True


def test_an_address_in_use_is_email_taken_and_audited_as_a_failure(
    db: Session,
    admin: User,
    make_user: Callable[..., User],
    create_as: Callable[..., Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
) -> None:
    existing = make_user(is_active=False)  # a deactivated account still holds its address
    username = f"t_{uuid4().hex[:8]}"

    # another case of the same address is the same address
    response = create_as(admin, email=existing.email.upper(), username=username)

    assert_error(response, 409, ErrorCode.EMAIL_TAKEN)
    assert users_with_email(db, existing.email) == 1
    assert db.scalar(select(User).where(User.username == username)) is None
    assert db.scalar(select(EmailOutbox).where(EmailOutbox.user_id == existing.id)) is None
    [entry] = audit_entries(db, admin)
    assert (entry.action, entry.result, entry.target_id) == (
        AuditAction.CREATE_USER,
        AuditResult.FAILURE,
        None,
    )
    assert entry.details == {
        "email": existing.email,
        "username": username,
        "role": "staff",
        "can_manage_content": False,
        "error": "EMAIL_TAKEN",
    }


def test_a_username_in_use_in_any_case_is_username_taken(
    db: Session,
    admin: User,
    make_user: Callable[..., User],
    create_as: Callable[..., Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
) -> None:
    existing = make_user()
    address = f"{uuid4().hex[:12]}@example.com"

    response = create_as(admin, email=address, username=existing.username.upper())

    assert_error(response, 409, ErrorCode.USERNAME_TAKEN)
    assert users_with_email(db, address) == 0
    [entry] = audit_entries(db, admin)
    assert entry.result == AuditResult.FAILURE and entry.details["error"] == "USERNAME_TAKEN"


def test_an_address_taken_after_the_check_is_still_email_taken_and_audited(
    db: Session,
    admin: User,
    make_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Another request adds the address between the check and the insert: the unique index
    refuses it, and only the insert is rolled back, so the FAILURE entry is still recorded."""
    existing = make_user()
    monkeypatch.setattr(staff, "email_exists", lambda *_: False)

    with pytest.raises(AppError) as raised:
        create_teacher(admin.id, existing.email, f"t_{uuid4().hex[:8]}", False, db)

    assert raised.value.code == ErrorCode.EMAIL_TAKEN
    [entry] = audit_entries(db, admin)
    assert entry.result == AuditResult.FAILURE and entry.details["error"] == "EMAIL_TAKEN"
    assert users_with_email(db, existing.email) == 1


def test_create_teacher_does_not_commit(
    db: Session, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    # not transaction_calls: SQLAlchemy reports releasing the insert's savepoint as a commit too
    commits: list[None] = []
    monkeypatch.setattr(db, "commit", lambda: commits.append(None))

    create_teacher(admin.id, f"{uuid4().hex[:12]}@example.com", f"t_{uuid4().hex[:8]}", False, db)

    assert commits == []


@pytest.mark.parametrize(
    "fields",
    [
        {"role": UserRole.STUDENT},
        {"role": UserRole.STAFF},
        {"role": UserRole.STAFF, "can_manage_content": True},
    ],
    ids=["student", "teacher", "content-manager"],
)
def test_non_admins_get_403_and_nothing_is_created(
    db: Session,
    make_user: Callable[..., User],
    create_as: Callable[..., Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
    fields: dict[str, Any],
) -> None:
    caller = make_user(**fields)
    address = f"{uuid4().hex[:12]}@example.com"

    response = create_as(caller, email=address)

    assert_error(response, 403, ErrorCode.FORBIDDEN_ROLE)
    assert users_with_email(db, address) == 0
    assert audit_entries(db, caller) == []


def test_a_deactivated_admin_gets_account_disabled(
    db: Session,
    make_user: Callable[..., User],
    create_as: Callable[..., Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
) -> None:
    disabled = make_user(role=UserRole.ADMIN, is_active=False)

    assert_error(create_as(disabled), 403, ErrorCode.ACCOUNT_DISABLED)
    assert audit_entries(db, disabled) == []


def test_without_a_token_the_request_is_401(
    client: TestClient, assert_error: Callable[[Response, int, ErrorCode], None]
) -> None:
    response = client.post(URL, json={"email": "a@example.com", "username": "teacher"})
    assert_error(response, 401, ErrorCode.NOT_AUTHENTICATED)


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"email": "not-an-email"}, "email"),
        ({"username": "ab"}, "username"),
        ({"username": "x" * 31}, "username"),
        ({"username": " padded "}, "username"),
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
