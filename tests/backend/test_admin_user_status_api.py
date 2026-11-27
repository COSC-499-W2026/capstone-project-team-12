"""/api/v1/admin/users/{id}/deactivate and /reactivate: an admin stops a teacher from signing in,
which revokes every refresh token, and lets them sign in again. Admin and student accounts are
refused. Each change is recorded in the admin audit log; a refused request records nothing."""

import threading
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import pytest
from conftest import audit_entries
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session

from app.accounts import login as login_module
from app.accounts.interface import set_staff_active
from app.accounts.login import login_account
from app.accounts.password import hash_password
from app.accounts.tokens import TokenPair, create_refresh_token
from app.errors import ErrorCode
from app.models.accounts import RefreshToken, User
from app.models.audit import AuditEvent
from app.models.enums import AuditAction, UserRole

PASSWORD = "TeacherPassword1"
ACTIONS = ["deactivate", "reactivate"]


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
    login: Callable[[str, str], Response],
    refresh: Callable[[str], Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
) -> None:
    signed_in = [login(teacher.email, PASSWORD) for _ in range(2)]  # two devices
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
        assert_error(refresh(device.cookies["refresh_token"]), 401, ErrorCode.NOT_AUTHENTICATED)
    assert_error(client.get("/api/v1/me/profile", headers=access), 403, ErrorCode.ACCOUNT_DISABLED)
    assert_error(login(teacher.email, PASSWORD), 403, ErrorCode.ACCOUNT_DISABLED)
    assert revoked(db, teacher) == [True, True]  # the refused login added no token

    [entry] = audit_entries(db, admin)
    assert (entry.action, entry.target_user_id, entry.target_class_id) == (
        AuditAction.DEACTIVATE,
        teacher.id,
        None,
    )
    assert entry.details == {"was_active": True}


def test_a_reactivated_teacher_can_sign_in_again(
    db: Session,
    admin: User,
    teacher: User,
    send: Callable[..., Response],
    login: Callable[[str, str], Response],
    refresh: Callable[[str], Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
) -> None:
    old_refresh_token = login(teacher.email, PASSWORD).cookies["refresh_token"]
    assert send("deactivate", admin, teacher.id).status_code == 200

    response = send("reactivate", admin, teacher.id)

    assert response.status_code == 200, response.text
    assert (response.json()["is_active"], response.json()["can_manage_content"]) == (True, True)
    db.refresh(teacher)
    assert teacher.is_active is True
    # the sign-in ended by the deactivation stays ended
    assert_error(refresh(old_refresh_token), 401, ErrorCode.NOT_AUTHENTICATED)
    signed_in = login(teacher.email, PASSWORD)
    assert signed_in.status_code == 200, signed_in.text
    assert refresh(signed_in.cookies["refresh_token"]).status_code == 200

    assert [(entry.action, entry.details) for entry in audit_entries(db, admin)] == [
        (AuditAction.DEACTIVATE, {"was_active": True}),
        (AuditAction.REACTIVATE, {"was_active": False}),
    ]


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.STUDENT])
def test_admin_and_student_accounts_are_refused_and_nothing_is_recorded(
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
    assert audit_entries(db, admin) == []


@pytest.mark.parametrize("action", ACTIONS)
def test_an_unknown_id_is_404_and_nothing_is_recorded(
    db: Session,
    admin: User,
    send: Callable[..., Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
    action: str,
) -> None:
    assert_error(send(action, admin, uuid4()), 404, ErrorCode.USER_NOT_FOUND)

    assert audit_entries(db, admin) == []


@pytest.mark.parametrize("action", ACTIONS)
def test_repeating_an_action_succeeds_and_is_recorded_again(
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
    assert entry.details == {"was_active": active}


@pytest.mark.parametrize("active", [False, True])
def test_set_staff_active_does_not_commit(
    db: Session, admin: User, teacher: User, transaction_calls: list[str], active: bool
) -> None:
    set_staff_active(admin.id, teacher.id, active, db)

    assert "commit" not in transaction_calls
    assert "rollback" not in transaction_calls


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
