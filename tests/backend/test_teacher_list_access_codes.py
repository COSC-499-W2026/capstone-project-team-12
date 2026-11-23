"""Tests for listing a classroom's access codes."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.models.accounts import ClassStaff, User
from app.models.engagement import AccessCode, Class
from app.models.enums import ClassStaffRole, UserRole

URL = "/api/v1/teacher/classes"


@pytest.mark.parametrize("archived", [False, True])
def test_owner_and_ta_can_list_all_codes_newest_first(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    make_access_code: Callable[..., AccessCode],
    auth_headers: Callable[[User], dict[str, str]],
    archived: bool,
) -> None:
    now = datetime.now(UTC)
    school_class = make_class(archived_at=now if archived else None)
    owner = make_user(role=UserRole.STAFF)
    ta = make_user(role=UserRole.STUDENT)
    make_class_staff(school_class, owner)
    make_class_staff(school_class, ta, role=ClassStaffRole.ASSISTANT)
    expired = make_access_code(
        school_class, expires_at=now - timedelta(days=1), created_at=now - timedelta(days=3)
    )
    deactivated = make_access_code(
        school_class, is_active=False, created_at=now - timedelta(days=2), max_uses=5, uses_count=2
    )
    active = make_access_code(school_class, created_at=now - timedelta(days=1))
    make_access_code(make_class())

    for user in (owner, ta):
        response = client.get(f"{URL}/{school_class.id}/access-codes", headers=auth_headers(user))
        assert response.status_code == 200, response.text
        rows = response.json()
        assert [row["id"] for row in rows] == [str(active.id), str(deactivated.id), str(expired.id)]
        for row, code in zip(rows, (active, deactivated, expired), strict=True):
            assert set(row) == {
                "id",
                "code",
                "expires_at",
                "is_active",
                "max_uses",
                "uses_count",
                "created_at",
            }
            assert row["code"] == code.code
            assert datetime.fromisoformat(row["expires_at"]) == code.expires_at
            assert row["is_active"] == code.is_active
            assert row["max_uses"] == code.max_uses
            assert row["uses_count"] == code.uses_count
            assert datetime.fromisoformat(row["created_at"]) == code.created_at


@pytest.mark.parametrize(
    ("role", "expected_status"),
    [(UserRole.STAFF, 403), (UserRole.STUDENT, 403), (None, 401)],
)
def test_access_code_listing_requires_class_staff(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    auth_headers: Callable[[User], dict[str, str]],
    role: UserRole | None,
    expected_status: int,
) -> None:
    school_class = make_class()
    headers = {}
    if role is not None:
        user = make_user(role=role)
        headers = auth_headers(user)
        if role == UserRole.STAFF:
            make_class_staff(make_class(), user)

    response = client.get(f"{URL}/{school_class.id}/access-codes", headers=headers)
    assert response.status_code == expected_status, response.text


def test_listing_codes_for_unknown_class_returns_404(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    response = client.get(f"{URL}/{uuid4()}/access-codes", headers=auth_headers(owner))
    assert response.status_code == 404, response.text
