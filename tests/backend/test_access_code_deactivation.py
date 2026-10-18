"""Tests for classroom access-code deactivation (Issue #84)."""

from datetime import UTC, datetime, timedelta
from time import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jwt import encode
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.engagement import AccessCode
from app.models.enums import ClassStaffRole, Language, UserRole


@pytest.fixture
def owner(make_user):
    """Create a classroom owner."""
    return make_user(role=UserRole.STAFF)


@pytest.fixture
def school_class(make_class, make_class_staff, owner):
    """Create a classroom assigned to its owner."""
    school_class = make_class(target_language=Language.JA)
    make_class_staff(
        school_class,
        owner,
        role=ClassStaffRole.OWNER,
    )
    return school_class


@pytest.fixture
def access_code(db: Session, school_class, owner):
    """Create an active access code with existing usage history."""
    code = AccessCode(
        class_id=school_class.id,
        code="ABC234XY",
        created_by=owner.id,
        expires_at=datetime.now(UTC) + timedelta(days=30),
        max_uses=10,
        uses_count=3,
        is_active=True,
    )
    db.add(code)
    db.flush()
    return code


def auth_headers(user):
    """Create authentication headers using the project's JWT settings."""
    token = encode(
        {
            "sub": str(user.id),
            "exp": int(time()) + 3600,
        },
        str(get_settings().access_token_secret_key),
        "HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def deactivate(client: TestClient, code_id, user, body=None):
    """Send an access-code deactivation request."""
    if body is None:
        body = {"is_active": False}

    return client.patch(
        f"/api/v1/teacher/access-codes/{code_id}",
        json=body,
        headers=auth_headers(user),
    )


def test_owner_can_deactivate_code(
    client: TestClient,
    db: Session,
    owner,
    access_code,
):
    """Classroom owners can deactivate their access codes."""
    response = deactivate(client, access_code.id, owner)

    assert response.status_code == 200
    assert response.json()["id"] == str(access_code.id)
    assert response.json()["is_active"] is False

    db.refresh(access_code)
    assert access_code.is_active is False


def test_repeated_deactivation_is_allowed(
    client: TestClient,
    owner,
    access_code,
):
    """Deactivating an already inactive code remains successful."""
    first = deactivate(client, access_code.id, owner)
    second = deactivate(client, access_code.id, owner)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["is_active"] is False


def test_deactivation_preserves_usage_history(
    client: TestClient,
    db: Session,
    owner,
    access_code,
):
    """Deactivation must not delete or reset access-code history."""
    original_code = access_code.code
    original_uses = access_code.uses_count
    original_class_id = access_code.class_id
    original_max_uses = access_code.max_uses

    response = deactivate(client, access_code.id, owner)

    assert response.status_code == 200

    db.refresh(access_code)

    assert access_code.code == original_code
    assert access_code.uses_count == original_uses
    assert access_code.class_id == original_class_id
    assert access_code.max_uses == original_max_uses
    assert access_code.is_active is False


def test_other_teacher_cannot_deactivate_code(
    client: TestClient,
    make_user,
    access_code,
):
    """Teachers without classroom membership cannot deactivate codes."""
    other_teacher = make_user(role=UserRole.STAFF)

    response = deactivate(client, access_code.id, other_teacher)

    assert response.status_code == 403


def test_student_cannot_deactivate_code(
    client: TestClient,
    make_user,
    access_code,
):
    """Students without classroom staff permissions cannot deactivate codes."""
    student = make_user(role=UserRole.STUDENT)

    response = deactivate(client, access_code.id, student)

    assert response.status_code == 403


def test_ta_can_deactivate_code(
    client: TestClient,
    db: Session,
    make_user,
    make_class_staff,
    school_class,
    access_code,
):
    """A teaching assistant can deactivate an access code."""
    ta = make_user(role=UserRole.STAFF)

    make_class_staff(
        school_class,
        ta,
        role=ClassStaffRole.ASSISTANT,
    )

    response = deactivate(client, access_code.id, ta)

    assert response.status_code == 200
    assert response.json()["is_active"] is False

    db.refresh(access_code)
    assert access_code.is_active is False


def test_student_role_ta_can_deactivate_code(
    client: TestClient,
    db: Session,
    make_user,
    make_class_staff,
    school_class,
    access_code,
):
    """A student assigned as a TA has classroom staff permissions."""
    ta = make_user(role=UserRole.STUDENT)

    make_class_staff(
        school_class,
        ta,
        role=ClassStaffRole.ASSISTANT,
    )

    response = deactivate(client, access_code.id, ta)

    assert response.status_code == 200
    assert response.json()["is_active"] is False

    db.refresh(access_code)
    assert access_code.is_active is False


def test_archived_class_code_cannot_be_deactivated(
    client: TestClient,
    db: Session,
    owner,
    school_class,
    access_code,
):
    """Archived classrooms must remain read-only."""
    school_class.archived_at = datetime.now(UTC)
    db.flush()

    response = deactivate(client, access_code.id, owner)

    assert response.status_code == 409

    db.refresh(access_code)
    assert access_code.is_active is True


def test_unauthorized_attempt_preserves_code(
    client: TestClient,
    db: Session,
    make_user,
    access_code,
):
    """Failed authorization must not modify the access code."""
    other_user = make_user(role=UserRole.STUDENT)

    original_uses = access_code.uses_count

    response = deactivate(client, access_code.id, other_user)

    assert response.status_code == 403

    db.refresh(access_code)
    assert access_code.is_active is True
    assert access_code.uses_count == original_uses


def test_nonexistent_code_returns_404(
    client: TestClient,
    owner,
):
    """A nonexistent access code returns HTTP 404."""
    response = deactivate(client, uuid4(), owner)

    assert response.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"is_active": True},
        {"is_active": None},
        {"is_active": "true"},
        {"is_active": False, "unexpected": "value"},
        {},
    ],
)
def test_invalid_request_body_is_rejected(
    client: TestClient,
    owner,
    access_code,
    body,
):
    """Invalid update requests must return HTTP 422."""
    response = deactivate(client, access_code.id, owner, body)

    assert response.status_code == 422


def test_unauthenticated_request_is_rejected(
    client: TestClient,
    access_code,
):
    """Unauthenticated requests must return HTTP 401."""
    response = client.patch(
        f"/api/v1/teacher/access-codes/{access_code.id}",
        json={"is_active": False},
    )

    assert response.status_code == 401
