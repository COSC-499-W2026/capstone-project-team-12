"""Tests for classroom access-code deactivation (Issue #84, P9)."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.engagement.interface import deactivate_access_code
from app.models.accounts import User
from app.models.engagement import AccessCode, Class
from app.models.enums import ClassStaffRole, UserRole

ROUTE = "/api/v1/teacher/access-codes"


def test_owner_deactivates_code(
    client: TestClient,
    db: Session,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_access_code: Callable[..., AccessCode],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)
    code = make_access_code(school_class)

    response = client.patch(f"{ROUTE}/{code.id}", headers=auth_headers(owner))

    assert response.status_code == 204
    assert response.content == b""
    db.refresh(code)
    assert code.is_active is False


def test_assistant_can_deactivate_code(
    client: TestClient,
    db: Session,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_access_code: Callable[..., AccessCode],
) -> None:
    assistant = make_user()
    school_class = make_class()
    make_class_staff(school_class, assistant, role=ClassStaffRole.ASSISTANT)
    code = make_access_code(school_class)

    response = client.patch(f"{ROUTE}/{code.id}", headers=auth_headers(assistant))

    assert response.status_code == 204
    db.refresh(code)
    assert code.is_active is False


def test_unrelated_user_cannot_deactivate_code(
    client: TestClient,
    db: Session,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    outsider = make_user(role=UserRole.STAFF)
    school_class = make_class()
    code = make_access_code(school_class)
    code_id = code.id

    db.commit()

    response = client.patch(f"{ROUTE}/{code_id}", headers=auth_headers(outsider))

    assert response.status_code == 403
    persisted_code = db.get(AccessCode, code_id)
    assert persisted_code is not None
    assert persisted_code.is_active is True


def test_student_cannot_deactivate_code(
    client: TestClient,
    db: Session,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    student = make_user()
    school_class = make_class()
    code = make_access_code(school_class)
    code_id = code.id

    db.commit()

    response = client.patch(f"{ROUTE}/{code_id}", headers=auth_headers(student))

    assert response.status_code == 403
    persisted_code = db.get(AccessCode, code_id)
    assert persisted_code is not None
    assert persisted_code.is_active is True


def test_archived_class_rejects_deactivation(
    client: TestClient,
    db: Session,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_access_code: Callable[..., AccessCode],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class(archived_at=datetime.now(UTC))
    make_class_staff(school_class, owner)
    code = make_access_code(school_class)
    code_id = code.id

    db.commit()

    response = client.patch(f"{ROUTE}/{code_id}", headers=auth_headers(owner))

    assert response.status_code == 409
    persisted_code = db.get(AccessCode, code_id)
    assert persisted_code is not None
    assert persisted_code.is_active is True


def test_missing_code_returns_not_found(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
) -> None:
    owner = make_user(role=UserRole.STAFF)

    response = client.patch(f"{ROUTE}/{uuid4()}", headers=auth_headers(owner))

    assert response.status_code == 404


def test_deactivation_is_idempotent(
    client: TestClient,
    db: Session,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_access_code: Callable[..., AccessCode],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)
    code = make_access_code(school_class)

    first = client.patch(f"{ROUTE}/{code.id}", headers=auth_headers(owner))
    second = client.patch(f"{ROUTE}/{code.id}", headers=auth_headers(owner))

    assert first.status_code == 204
    assert second.status_code == 204
    db.refresh(code)
    assert code.is_active is False


def test_deactivation_preserves_existing_enrolment(
    client: TestClient,
    db: Session,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_access_code: Callable[..., AccessCode],
    make_enrollment: Callable[..., object],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    student = make_user()
    school_class = make_class()
    make_class_staff(school_class, owner)
    code = make_access_code(school_class)
    enrolment = make_enrollment(student, school_class, access_code_id=code.id)

    response = client.patch(f"{ROUTE}/{code.id}", headers=auth_headers(owner))

    assert response.status_code == 204
    db.refresh(enrolment)
    assert enrolment.is_active is True
    assert enrolment.access_code_id == code.id


def test_unauthenticated_request_is_rejected(
    client: TestClient,
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
) -> None:
    code = make_access_code(make_class())

    response = client.patch(f"{ROUTE}/{code.id}")

    assert response.status_code == 401


def test_service_does_not_commit(
    db: Session,
    transaction_calls: list[str],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_access_code: Callable[..., AccessCode],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)
    code = make_access_code(school_class)

    transaction_calls.clear()
    result = deactivate_access_code(code.id, owner, db)

    assert result.id == code.id
    assert result.is_active is False
    assert "commit" not in transaction_calls
    assert "rollback" not in transaction_calls
