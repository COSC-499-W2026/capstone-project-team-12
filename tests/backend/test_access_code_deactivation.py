"""Tests for classroom access-code deactivation (Issue #84)."""

from datetime import UTC, datetime, timedelta
from time import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jwt import encode
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.accounts import ClassStaff
from app.models.engagement import AccessCode
from app.models.enums import ClassStaffRole, Language, UserRole


@pytest.fixture
def owner(make_user):
    return make_user(role=UserRole.STAFF)


@pytest.fixture
def school_class(make_class, make_class_staff, owner):
    school_class = make_class(target_language=Language.JA)
    make_class_staff(school_class, owner, role=ClassStaffRole.OWNER)
    return school_class


@pytest.fixture
def access_code(db: Session, school_class, owner):
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
    original_code = access_code.code
    original_uses = access_code.uses_count
    original_class_id = access_code.class_id

    response = deactivate(client, access_code.id, owner)

    assert response.status_code == 200

    db.refresh(access_code)
    assert access_code.code == original_code
    assert access_code.uses_count == original_uses
    assert access_code.class_id == original_class_id
    assert access_code.is_active is False


def test_other_teacher_cannot_deactivate_code(
    client: TestClient,
    make_user,
    access_code,
):
    other_teacher = make_user(role=UserRole.STAFF)

    response = deactivate(client, access_code.id, other_teacher)

    assert response.status_code == 403


def test_student_cannot_deactivate_code(
    client: TestClient,
    make_user,
    access_code,
):
    student = make_user(role=UserRole.STUDENT)

    response = deactivate(client, access_code.id, student)

    assert response.status_code == 403


def test_ta_cannot_deactivate_code(
    client: TestClient,
    db: Session,
    make_user,
    school_class,
    access_code,
):
    ta = make_user(role=UserRole.STAFF)

    db.add(
        ClassStaff(
            class_id=school_class.id,
            user_id=ta.id,
            role=ClassStaffRole.ASSISTANT,
        )
    )
    db.flush()

    response = deactivate(client, access_code.id, ta)

    assert response.status_code == 403


def test_nonexistent_code_returns_404(
    client: TestClient,
    owner,
):
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
    response = deactivate(client, access_code.id, owner, body)

    assert response.status_code == 422


def test_unauthenticated_request_is_rejected(
    client: TestClient,
    access_code,
):
    response = client.patch(
        f"/api/v1/teacher/access-codes/{access_code.id}",
        json={"is_active": False},
    )

    assert response.status_code == 401

