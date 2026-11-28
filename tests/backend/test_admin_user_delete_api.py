"""DELETE /api/v1/admin/users/{id}: an admin deletes an account, which anonymises it. Its
results stay for the class statistics; owners of active classes, admin accounts, a mistyped
username and an account already deleted are refused, and a deletion is recorded in the admin
audit log."""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from conftest import audit_entries
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from app.accounts import anonymise as anonymise_module
from app.accounts.interface import anonymise_user
from app.accounts.tokens import create_refresh_token
from app.engagement.interface import create_class
from app.errors import AppError, ErrorCode
from app.models.accounts import INVITATION_TTL, ClassStaff, ClassStaffInvitation, RefreshToken, User
from app.models.audit import AuditEvent
from app.models.email import EmailOutbox, EmailToken
from app.models.engagement import Class
from app.models.enums import (
    AuditAction,
    ClassStaffRole,
    EmailKind,
    InvitationStatus,
    Language,
    LocationStatus,
    UserRole,
)
from app.models.game import GameSession, SessionLocation
from app.services.email import EmailService

# a completed location with the last position the student sent there
PLAYED = {
    "status": LocationStatus.COMPLETED,
    "points_awarded": 100,
    "last_lat": 49.9394,
    "last_lng": -119.3948,
    "last_distance_m": 4.0,
}


@pytest.fixture
def delete_user(
    client: TestClient, auth_headers: Callable[[User], dict[str, str]]
) -> Callable[[User, UUID, str], Response]:
    """DELETE /admin/users/{target} as `caller`, typing `username`."""

    def send(caller: User, target: UUID, username: str) -> Response:
        url = f"/api/v1/admin/users/{target}"
        # client.delete() can't send a body
        return client.request(
            "DELETE", url, json={"username": username}, headers=auth_headers(caller)
        )

    return send


def count(db: Session, model: type[EmailToken] | type[EmailOutbox], user: User) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.user_id == user.id)) or 0


def test_a_student_is_anonymised_and_keeps_their_results(
    db: Session,
    admin: User,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    delete_user: Callable[..., Response],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    student = make_user(
        password_hash="hash",
        email_verified_at=now,
        last_login_at=now,
        preferred_language=Language.JA,
    )
    other = make_user()
    create_refresh_token(student.id, db)
    token_hash = hashlib.sha256(uuid4().bytes).hexdigest()
    token = {"purpose": EmailKind.VERIFY_EMAIL, "token_hash": token_hash, "expires_at": now}
    db.add(EmailToken(user_id=student.id, **token))
    for user in (student, other):
        EmailService.queue_email(user, EmailKind.VERIFY_EMAIL, db)
    invited = {
        "class_id": make_class().id,
        "invitee_id": student.id,
        "email": student.email,
        "invited_by": make_user(role=UserRole.STAFF).id,
        "expires_at": now + INVITATION_TTL,
    }
    pending = ClassStaffInvitation(**invited)
    accepted = ClassStaffInvitation(**invited, status=InvitationStatus.ACCEPTED, responded_at=now)
    db.add_all([pending, accepted])
    row, other_row = (
        make_session_location(
            make_game_session(user), 1, completed_at=now, last_ping_at=now, **PLAYED
        )
        for user in (student, other)
    )
    deleted_photos: list[UUID] = []
    monkeypatch.setattr(
        anonymise_module, "delete_user_photos", lambda user_id, _: deleted_photos.append(user_id)
    )

    response = delete_user(admin, student.id, student.username)

    assert (response.status_code, response.content) == (204, b""), response.text
    db.refresh(student)
    assert (student.email, student.username) == (
        f"deleted-{student.id.hex}@deleted.invalid",
        f"deleted-user-{student.id.hex[:17]}",
    )
    for cleared in ("password_hash", "email_verified_at", "last_login_at", "preferred_language"):
        assert getattr(student, cleared) is None, cleared
    assert student.is_active is False
    tokens = select(RefreshToken.revoked_at).where(RefreshToken.user_id == student.id)
    assert all(revoked_at is not None for revoked_at in db.scalars(tokens))
    assert (count(db, EmailToken, student), count(db, EmailOutbox, student)) == (0, 0)
    assert count(db, EmailOutbox, other) == 1
    for invitation in (pending, accepted):
        db.refresh(invitation)
    assert [(invitation.status, invitation.email) for invitation in (pending, accepted)] == [
        (InvitationStatus.CANCELLED, None),
        (InvitationStatus.ACCEPTED, None),
    ]
    db.refresh(row)
    db.refresh(other_row)
    assert (row.last_lat, row.last_lng, row.last_distance_m, row.last_ping_at) == (None,) * 4
    assert (row.status, row.points_awarded) == (LocationStatus.COMPLETED, 100)
    assert (other_row.last_lat, other_row.last_lng) == (PLAYED["last_lat"], PLAYED["last_lng"])
    assert deleted_photos == [student.id]
    [entry] = audit_entries(db, admin)
    assert (entry.action, entry.target_user_id, entry.details) == (
        AuditAction.DELETE_USER,
        student.id,
        {"role": "student"},
    )


def test_an_owner_of_an_active_class_is_refused_until_it_is_archived(
    db: Session,
    admin: User,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    delete_user: Callable[..., Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    owned, archived, assisted = (
        make_class(),
        make_class(archived_at=datetime.now(UTC)),
        make_class(),
    )
    make_class_staff(owned, teacher)
    make_class_staff(archived, teacher)
    make_class_staff(assisted, teacher, role=ClassStaffRole.ASSISTANT)  # assisting doesn't block

    response = delete_user(admin, teacher.id, teacher.username)

    assert_error(response, 409, ErrorCode.OWNS_ACTIVE_CLASS)
    assert response.json()["error"]["details"] == [{"class_id": str(owned.id)}]
    db.refresh(teacher)
    assert teacher.is_active is True
    owned.archived_at = datetime.now(UTC)
    db.flush()
    assert delete_user(admin, teacher.id, teacher.username).status_code == 204
    [entry] = audit_entries(db, admin)  # only the deletion that went through
    assert entry.details == {"role": "staff"}


@pytest.mark.parametrize(
    ("case", "status", "code"),
    [
        ("unknown-id", 404, ErrorCode.USER_NOT_FOUND),
        ("admin", 403, ErrorCode.FORBIDDEN_ROLE),
        ("mistyped", 422, ErrorCode.USERNAME_MISMATCH),
    ],
)
def test_a_refused_deletion_changes_nothing_and_records_nothing(
    db: Session,
    admin: User,
    make_user: Callable[..., User],
    delete_user: Callable[..., Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
    case: str,
    status: int,
    code: ErrorCode,
) -> None:
    target = make_user(role=UserRole.ADMIN if case == "admin" else UserRole.STUDENT)
    target_id = uuid4() if case == "unknown-id" else target.id
    # usernames are unique in any case, but the confirmation must match exactly
    typed = target.username.upper() if case == "mistyped" else target.username

    assert_error(delete_user(admin, target_id, typed), status, code)

    db.refresh(target)
    assert (target.is_active, target.username.startswith("deleted-")) == (True, False)
    assert audit_entries(db, admin) == []


def test_a_deleted_account_cannot_be_deleted_again_or_reactivated(
    db: Session,
    admin: User,
    make_user: Callable[..., User],
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    delete_user: Callable[..., Response],
    assert_error: Callable[[Response, int, ErrorCode], None],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    assert delete_user(admin, teacher.id, teacher.username).status_code == 204
    db.refresh(teacher)

    again = delete_user(admin, teacher.id, teacher.username)
    url = f"/api/v1/admin/users/{teacher.id}/reactivate"
    reactivate = client.post(url, headers=auth_headers(admin))

    assert_error(again, 404, ErrorCode.USER_NOT_FOUND)
    assert_error(reactivate, 404, ErrorCode.USER_NOT_FOUND)
    db.refresh(teacher)
    assert teacher.is_active is False
    assert len(audit_entries(db, admin)) == 1  # the first deletion only


@pytest.mark.parametrize("delete_first", [True, False], ids=["delete-first", "create-first"])
def test_a_class_created_during_a_deletion_never_has_a_deleted_owner(
    test_engine: Engine, run_together: Callable[..., list[Any]], delete_first: bool
) -> None:
    # committed, so the deletion and the class creation can run in two transactions at once
    with Session(test_engine, expire_on_commit=False) as setup:
        admin, teacher = (
            User(email=f"{uuid4().hex}@example.com", username=f"u_{uuid4().hex[:8]}", role=role)
            for role in (UserRole.ADMIN, UserRole.STAFF)
        )
        setup.add_all([admin, teacher])
        setup.commit()

    def delete_account(session: Session) -> None:
        anonymise_user(admin.id, teacher.id, teacher.username, session)

    def create(session: Session) -> Class:
        # read before taking the lock, as the route's sign-in check is
        signed_in = session.scalars(select(User).filter_by(id=teacher.id)).one()
        return create_class(signed_in, "JAPN 101", "2026W1", Language.JA, session)

    first, second = (delete_account, create) if delete_first else (create, delete_account)
    try:
        # the second starts while the first holds the user lock, and waits for its commit
        _, refused = run_together(first, (second, 0.1), hold=0.5)
        assert isinstance(refused, AppError), "both went through"
        expected = ErrorCode.ACCOUNT_DISABLED if delete_first else ErrorCode.OWNS_ACTIVE_CLASS
        assert refused.code == expected
    finally:
        with Session(test_engine) as cleanup:
            owned = list(cleanup.scalars(select(ClassStaff.class_id).filter_by(user_id=teacher.id)))
            cleanup.execute(delete(ClassStaff).filter_by(user_id=teacher.id))
            cleanup.execute(delete(Class).where(Class.id.in_(owned)))
            cleanup.execute(delete(AuditEvent).filter_by(actor_id=admin.id))
            cleanup.execute(delete(User).where(User.id.in_([admin.id, teacher.id])))
            cleanup.commit()


def test_anonymise_user_does_not_commit(
    db: Session, admin: User, make_user: Callable[..., User], transaction_calls: list[str]
) -> None:
    target = make_user()

    anonymise_user(admin.id, target.id, target.username, db)  # the photo deletion is a no-op

    assert "commit" not in transaction_calls
