"""/api/v1/admin/users/{id}/deactivate and /reactivate: an admin stops a teacher from signing in,
which revokes every refresh token, and lets them sign in again. Admin and student accounts are
refused, and every attempt is written to the audit log."""

import threading
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session

from app.accounts import login as login_module
from app.accounts.login import login_account
from app.accounts.password import hash_password
from app.accounts.staff import set_staff_active
from app.accounts.tokens import TokenPair, create_refresh_token
from app.errors import ErrorCode
from app.models.accounts import RefreshToken, User
from app.models.audit import AuditEvent
from app.models.enums import AuditAction, AuditResult, UserRole

PASSWORD = "TeacherPassword1"
ACTIONS = {"deactivate": AuditAction.DEACTIVATE_USER, "reactivate": AuditAction.REACTIVATE_USER}


@pytest.fixture
def admin(make_user: Callable[..., User]) -> User:
    return make_user(role=UserRole.ADMIN)


@pytest.fixture
def teacher(make_user: Callable[..., User]) -> User:
    """An active teacher with content rights who can sign in with PASSWORD."""
    return make_user(
        role=UserRole.STAFF, can_manage_content=True, password_hash=hash_password(PASSWORD)
    )


@pytest.fixture
def send(
    client: TestClient, auth_headers: Callable[[User], dict[str, str]]
) -> Callable[[str, User, UUID], Response]:
    """POST /admin/users/{target}/{action} as `caller`."""

    def post(action: str, caller: User, target: UUID) -> Response:
        url = f"/api/v1/admin/users/{target}/{action}"
        return client.post(url, headers=auth_headers(caller))

    return post


def login(client: TestClient, user: User) -> Response:
    response = client.post(
        "/api/v1/auth/login", data={"username": user.email, "password": PASSWORD}
    )
    client.cookies.clear()  # tests send the refresh cookie themselves
    return response


def refresh(client: TestClient, refresh_token: str) -> Response:
    response = client.post(
        "/api/v1/auth/refresh", headers={"Cookie": f"refresh_token={refresh_token}"}
    )
    client.cookies.clear()
    return response


def audit_entries(db: Session, actor: User) -> list[AuditEvent]:
    entries = select(AuditEvent).where(AuditEvent.actor_id == actor.id)
    return list(db.scalars(entries.order_by(AuditEvent.created_at)))


def revoked(db: Session, user: User) -> list[bool]:
    """Whether each of the user's refresh tokens is revoked."""
    tokens = select(RefreshToken.revoked_at).where(RefreshToken.user_id == user.id)
    return [at is not None for at in db.scalars(tokens)]


def test_a_deactivated_teacher_is_signed_out_everywhere_and_cannot_sign_in(
    db: Session,
    client: TestClient,
    admin: User,
    teacher: User,
    send: Callable[..., Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
) -> None:
    signed_in = [login(client, teacher) for _ in range(2)]  # two devices
    assert [response.status_code for response in signed_in] == [200, 200]
    access = {"Authorization": f"Bearer {signed_in[0].json()['access_token']}"}

    response = send("deactivate", admin, teacher.id)

    assert response.status_code == 200, response.text
    body = response.json()
    # content rights are kept for when the account is reactivated
    assert (body["is_active"], body["can_manage_content"]) == (False, True)
    db.refresh(teacher)
    assert teacher.is_active is False
    assert revoked(db, teacher) == [True, True]
    for device in signed_in:
        assert_error(
            refresh(client, device.cookies["refresh_token"]), 401, ErrorCode.NOT_AUTHENTICATED
        )
    assert_error(client.get("/api/v1/me/profile", headers=access), 403, ErrorCode.ACCOUNT_DISABLED)
    assert_error(login(client, teacher), 403, ErrorCode.ACCOUNT_DISABLED)
    assert revoked(db, teacher) == [True, True]  # the refused login added no token

    [entry] = audit_entries(db, admin)
    assert (entry.action, entry.result, entry.target_id, entry.ip_address) == (
        AuditAction.DEACTIVATE_USER,
        AuditResult.SUCCESS,
        teacher.id,
        "testclient",
    )
    assert entry.details == {
        "email": teacher.email,
        "username": teacher.username,
        "role": "staff",
        "was_active": True,
    }


def test_a_reactivated_teacher_can_sign_in_again(
    db: Session,
    client: TestClient,
    admin: User,
    teacher: User,
    send: Callable[..., Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
) -> None:
    old_refresh_token = login(client, teacher).cookies["refresh_token"]
    assert send("deactivate", admin, teacher.id).status_code == 200

    response = send("reactivate", admin, teacher.id)

    assert response.status_code == 200, response.text
    assert (response.json()["is_active"], response.json()["can_manage_content"]) == (True, True)
    db.refresh(teacher)
    assert teacher.is_active is True
    # the sign-in ended by the deactivation stays ended
    assert_error(refresh(client, old_refresh_token), 401, ErrorCode.NOT_AUTHENTICATED)
    signed_in = login(client, teacher)
    assert signed_in.status_code == 200, signed_in.text
    assert refresh(client, signed_in.cookies["refresh_token"]).status_code == 200

    entries = audit_entries(db, admin)
    assert [(entry.action, entry.result, entry.details["was_active"]) for entry in entries] == [
        (AuditAction.DEACTIVATE_USER, AuditResult.SUCCESS, True),
        (AuditAction.REACTIVATE_USER, AuditResult.SUCCESS, False),
    ]


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.STUDENT])
def test_admin_and_student_accounts_are_refused_and_audited_as_denied(
    db: Session,
    admin: User,
    make_user: Callable[..., User],
    send: Callable[..., Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
    action: str,
    role: UserRole,
) -> None:
    was_active = action == "deactivate"  # each starts in the state the action would change
    target = make_user(role=role, is_active=was_active)
    create_refresh_token(target.id, db)

    assert_error(send(action, admin, target.id), 403, ErrorCode.FORBIDDEN_ROLE)

    db.refresh(target)
    assert (target.is_active, revoked(db, target)) == (was_active, [False])
    [entry] = audit_entries(db, admin)
    assert (entry.action, entry.result, entry.target_id) == (
        ACTIONS[action],
        AuditResult.DENIED,
        target.id,
    )
    assert entry.details == {
        "email": target.email,
        "username": target.username,
        "role": role.value,
        "was_active": was_active,
        "error": "FORBIDDEN_ROLE",
    }


@pytest.mark.parametrize("action", ACTIONS)
def test_an_unknown_id_is_user_not_found_and_audited_as_a_failure(
    db: Session,
    admin: User,
    send: Callable[..., Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
    action: str,
) -> None:
    unknown = uuid4()

    assert_error(send(action, admin, unknown), 404, ErrorCode.USER_NOT_FOUND)

    [entry] = audit_entries(db, admin)
    assert (entry.action, entry.result, entry.target_id, entry.details) == (
        ACTIONS[action],
        AuditResult.FAILURE,
        unknown,
        {"error": "USER_NOT_FOUND"},
    )


@pytest.mark.parametrize("action", ACTIONS)
def test_repeating_an_action_succeeds_and_is_audited_again(
    db: Session,
    admin: User,
    make_user: Callable[..., User],
    send: Callable[..., Response],
    action: str,
) -> None:
    active = action == "reactivate"
    target = make_user(role=UserRole.STAFF, is_active=active)  # already in the end state
    create_refresh_token(target.id, db)  # e.g. left over on an account deactivated directly

    response = send(action, admin, target.id)

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is active
    assert revoked(db, target) == [not active]  # deactivating again still revokes it
    [entry] = audit_entries(db, admin)
    assert (entry.result, entry.details["was_active"]) == (AuditResult.SUCCESS, active)


@pytest.mark.parametrize("action", ACTIONS)
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
def test_only_active_admins_can_change_a_status(
    db: Session,
    make_user: Callable[..., User],
    send: Callable[..., Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
    action: str,
    fields: dict[str, Any],
    code: ErrorCode,
) -> None:
    caller = make_user(**fields)
    target = make_user(role=UserRole.STAFF, is_active=action == "deactivate")

    assert_error(send(action, caller, target.id), 403, code)

    db.refresh(target)
    assert target.is_active is (action == "deactivate")
    assert audit_entries(db, caller) == []


@pytest.mark.parametrize("action", ACTIONS)
def test_without_a_token_the_request_is_401(
    client: TestClient,
    teacher: User,
    assert_error: Callable[[Response, int, ErrorCode], None],
    action: str,
) -> None:
    response = client.post(f"/api/v1/admin/users/{teacher.id}/{action}")
    assert_error(response, 401, ErrorCode.NOT_AUTHENTICATED)


@pytest.mark.parametrize("active", [False, True])
def test_the_service_does_not_commit(
    db: Session, admin: User, teacher: User, monkeypatch: pytest.MonkeyPatch, active: bool
) -> None:
    commits: list[None] = []
    monkeypatch.setattr(db, "commit", lambda: commits.append(None))

    set_staff_active(admin.id, teacher.id, active, db)

    assert commits == []


def test_a_login_in_progress_has_its_token_revoked_by_the_deactivation(
    test_engine: Engine, run_together: Callable[..., list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A login has added its refresh token but not committed when the deactivation starts. The
    deactivation waits on the user lock the login holds, then revokes that token too; without
    the wait it couldn't see the uncommitted row, and the token would outlive the deactivation.
    """
    admin, teacher = (
        User(
            email=f"{uuid4().hex[:12]}@example.com",
            username=f"user_{uuid4().hex[:8]}",
            role=role,
            password_hash=hash_password(PASSWORD),
        )
        for role in (UserRole.ADMIN, UserRole.STAFF)
    )
    with Session(test_engine, expire_on_commit=False) as setup:
        setup.add_all([admin, teacher])
        setup.commit()
    inserted = threading.Event()
    create = login_module.create_refresh_token

    def create_and_signal(user_id: UUID, db: Session) -> tuple[str, UUID]:
        created = create(user_id, db)
        inserted.set()  # flushed, not committed: run_together holds the transaction open
        return created

    monkeypatch.setattr(login_module, "create_refresh_token", create_and_signal)
    form = OAuth2PasswordRequestForm(username=teacher.email, password=PASSWORD)

    def deactivate_once_signed_in(session: Session) -> User:
        assert inserted.wait(timeout=10), "the login never added its token"
        return set_staff_active(admin.id, teacher.id, False, session)

    try:
        signed_in, deactivated = run_together(
            lambda session: login_account(form, session), deactivate_once_signed_in, hold=0.5
        )

        assert isinstance(signed_in, TokenPair) and isinstance(deactivated, User)
        with Session(test_engine) as check:
            assert check.get_one(User, teacher.id).is_active is False
            assert revoked(check, teacher) == [True]
    finally:
        with Session(test_engine) as cleanup:
            cleanup.execute(text("SET lock_timeout = '10s'"))  # a stuck thread can't hang pytest
            cleanup.execute(delete(AuditEvent).where(AuditEvent.actor_id == admin.id))
            cleanup.execute(delete(RefreshToken).where(RefreshToken.user_id == teacher.id))
            cleanup.execute(delete(User).where(User.id.in_([admin.id, teacher.id])))
            cleanup.commit()
