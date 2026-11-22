"""/api/v1/admin/staff: an admin creates a teacher account (its set-password email is queued
and the new account is recorded in the admin audit log) and lists the staff and admin
accounts."""

from collections.abc import Callable
from datetime import UTC, datetime
from itertools import count
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


def every_listed_account(
    client: TestClient, headers: dict[str, str], **params: str
) -> list[dict[str, Any]]:
    """The items of every page of GET /admin/staff, in order."""
    items: list[dict[str, Any]] = []
    for page in count(1):
        paging = {"page": page, "page_size": 100}
        response = client.get(URL, params=params | paging, headers=headers)
        assert response.status_code == 200, response.text
        items += response.json()["items"]
        if not response.json()["has_next_page"]:
            break
    return items


LISTED = (UserRole.STAFF, UserRole.ADMIN)


def staff_count(db: Session) -> int:
    staff = select(func.count()).select_from(User).where(User.role.in_(LISTED))
    return db.scalar(staff) or 0


def test_a_created_teacher_shows_in_the_staff_list(
    client: TestClient,
    admin: User,
    auth_headers: Callable[[User], dict[str, str]],
    create_as: Callable[..., Response],
) -> None:
    created = create_as(admin, can_manage_content=True)
    assert created.status_code == 201, created.text

    assert created.json() in every_listed_account(client, auth_headers(admin))


def test_the_list_has_every_staff_and_admin_account_by_username_in_any_case(
    db: Session,
    client: TestClient,
    admin: User,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    tag = uuid4().hex[:8]  # other tests' accounts may be listed too, so ours are told apart
    signed_in = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
    make_user(role=UserRole.STAFF, username=f"b_{tag}", last_login_at=signed_in)
    make_user(role=UserRole.STAFF, username=f"C_{tag}", is_active=False)
    make_user(role=UserRole.STAFF, username=f"a_{tag}", can_manage_content=True)
    make_user(role=UserRole.STUDENT, username=f"s_{tag}")
    make_user(role=UserRole.ADMIN, username=f"d_{tag}")

    listed = every_listed_account(client, auth_headers(admin))

    assert all(item["role"] in LISTED for item in listed)
    assert len(listed) == staff_count(db)
    ours = [item for item in listed if item["username"].endswith(tag)]
    fields = ("username", "role", "can_manage_content", "is_active", "last_login_at")
    # the deactivated teacher is listed, so it can be reactivated; the student is not
    assert [tuple(item[field] for field in fields) for item in ours] == [
        (f"a_{tag}", "staff", True, True, None),
        (f"b_{tag}", "staff", False, True, "2026-11-20T09:30:00Z"),
        (f"C_{tag}", "staff", False, False, None),
        (f"d_{tag}", "admin", False, True, None),
    ]
    assert audit_entries(db, admin) == []  # reading the list is not audited


def test_the_list_can_be_searched_by_part_of_the_email_in_any_case(
    client: TestClient,
    admin: User,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    tag = uuid4().hex[:8]
    teacher = make_user(role=UserRole.STAFF, email=f"tanaka.{tag}@school.example")
    other_admin = make_user(role=UserRole.ADMIN, email=f"ops.{tag}@school.example")
    make_user(role=UserRole.STAFF, email=f"tanaka.{tag}@other.example")
    make_user(email=f"student.{tag}@school.example")  # students are never listed

    found = every_listed_account(client, auth_headers(admin), email=f"{tag}@SCHOOL")

    assert sorted(item["id"] for item in found) == sorted([str(teacher.id), str(other_admin.id)])
    # % and _ are plain characters, not wildcards
    assert every_listed_account(client, auth_headers(admin), email=f"%{tag}") == []
    assert every_listed_account(client, auth_headers(admin), email=f"_{tag}") == []


def test_the_list_is_paged(
    db: Session,
    client: TestClient,
    admin: User,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    for _ in range(3):
        make_user(role=UserRole.STAFF)
    total = staff_count(db)

    def get_page(**params: int) -> dict[str, Any]:
        response = client.get(URL, params=params, headers=auth_headers(admin))
        assert response.status_code == 200, response.text
        return dict(response.json())

    first = get_page()  # page 1 of 20 by default
    assert (first["page"], first["page_size"], first["total"]) == (1, 20, total)
    assert (len(first["items"]), first["has_next_page"]) == (min(total, 20), total > 20)

    pages = [get_page(page=number, page_size=1) for number in (1, total, total + 1)]
    assert [(len(page["items"]), page["has_next_page"]) for page in pages] == [
        (1, True),
        (1, False),
        (0, False),  # past the end
    ]
    assert pages[0]["items"] == first["items"][:1]


@pytest.mark.parametrize(
    "params",
    [{"page": 0}, {"page_size": 0}, {"page_size": 101}, {"page": "first"}, {"email": ""}],
)
def test_invalid_paging_is_422(
    client: TestClient,
    admin: User,
    auth_headers: Callable[[User], dict[str, str]],
    assert_error: Callable[[Response, int, ErrorCode], None],
    params: dict[str, Any],
) -> None:
    response = client.get(URL, params=params, headers=auth_headers(admin))
    assert_error(response, 422, ErrorCode.VALIDATION_ERROR)


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
def test_only_active_admins_can_list_staff(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
    assert_error: Callable[[Response, int, ErrorCode], None],
    fields: dict[str, Any],
    code: ErrorCode,
) -> None:
    response = client.get(URL, headers=auth_headers(make_user(**fields)))
    assert_error(response, 403, code)
