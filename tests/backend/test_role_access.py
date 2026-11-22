"""The auth dependencies (A4, #71), through a small app that uses each one on a route."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import uuid4

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from jwt import encode
from sqlalchemy.orm import Session

from app.accounts.interface import (
    can_manage_content,
    get_current_user,
    is_class_staff,
    require_admin,
    require_class_role,
)
from app.config import get_settings
from app.db import get_db
from app.errors import ErrorCode, add_error_handlers
from app.models.accounts import ClassStaff, User
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, UserRole

router = APIRouter()


@router.post("/restricted")
def needs_login(user: Annotated[User, Depends(get_current_user)]) -> None:
    return None


@router.post("/staff/{class_id}", dependencies=[Depends(require_class_role(*ClassStaffRole))])
def class_staff_only() -> None:
    return None


@router.post("/owner/{class_id}", dependencies=[Depends(require_class_role(ClassStaffRole.OWNER))])
def class_owner_only() -> None:
    return None


@router.post("/content", dependencies=[Depends(can_manage_content)])
def content_managers_only() -> None:
    return None


@router.post("/admin", dependencies=[Depends(require_admin)])
def admins_only() -> None:
    return None


@pytest.fixture
def post(db: Session, auth_headers: Callable[[User], dict[str, str]]) -> Callable[..., Response]:
    """POST to a route of the test app as the user, or with a raw token."""
    app = FastAPI()
    add_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    def send(path: str, user: User | None = None, token: str | None = None) -> Response:
        headers = auth_headers(user) if user else {"Authorization": f"Bearer {token}"}
        return client.post(path, headers=headers)

    return send


def token(claims: dict[str, Any], key: str | None = None) -> str:
    return encode(claims, key or str(get_settings().access_token_secret_key), "HS256")


def expiry(hours: int) -> int:
    return int((datetime.now(UTC) + timedelta(hours=hours)).timestamp())


def test_a_valid_token_gets_through(
    post: Callable[..., Response], make_user: Callable[..., User]
) -> None:
    assert post("/restricted", make_user()).status_code == 200


@pytest.mark.parametrize(
    "make_token",
    [
        lambda user_id: token({"sub": user_id, "exp": expiry(-1)}),
        lambda user_id: token(
            {"sub": user_id, "exp": expiry(1)}, key="a different key of at least 32 bytes"
        ),
        lambda user_id: token({"sub": user_id}),
        lambda user_id: token({"exp": expiry(1)}),
        lambda user_id: token({"sub": "not a uuid", "exp": expiry(1)}),
        lambda user_id: token({"sub": str(uuid4()), "exp": expiry(1)}),
    ],
    ids=["expired", "another key", "no expiry", "no subject", "not a uuid", "no such user"],
)
def test_a_bad_token_is_refused(
    post: Callable[..., Response],
    make_user: Callable[..., User],
    assert_error: Callable[..., None],
    make_token: Callable[[str], str],
) -> None:
    response = post("/restricted", token=make_token(str(make_user().id)))

    assert_error(response, 401, ErrorCode.NOT_AUTHENTICATED)


def test_a_disabled_account_is_refused(
    post: Callable[..., Response],
    make_user: Callable[..., User],
    assert_error: Callable[..., None],
) -> None:
    user = make_user(is_active=False)

    assert_error(post("/restricted", user), 403, ErrorCode.ACCOUNT_DISABLED)


@pytest.mark.parametrize(
    "role, path, status",
    [
        (ClassStaffRole.OWNER, "owner", 200),
        (ClassStaffRole.OWNER, "staff", 200),
        (ClassStaffRole.ASSISTANT, "staff", 200),
        (ClassStaffRole.ASSISTANT, "owner", 403),
        (None, "staff", 403),
    ],
)
def test_class_roles(
    post: Callable[..., Response],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    role: ClassStaffRole | None,
    path: str,
    status: int,
) -> None:
    user, school_class = make_user(), make_class()
    if role is not None:
        make_class_staff(school_class, user, role=role)

    assert post(f"/{path}/{school_class.id}", user).status_code == status


def test_a_role_in_another_class_doesnt_count(
    post: Callable[..., Response],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    assert_error: Callable[..., None],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    make_class_staff(make_class(), teacher)

    response = post(f"/owner/{make_class().id}", teacher)

    assert_error(response, 403, ErrorCode.FORBIDDEN_ROLE)


def test_an_unknown_class_is_not_found(
    post: Callable[..., Response],
    make_user: Callable[..., User],
    assert_error: Callable[..., None],
) -> None:
    assert_error(post(f"/staff/{uuid4()}", make_user()), 404, ErrorCode.NOT_FOUND)


@pytest.mark.parametrize(
    "fields, status",
    [
        ({"role": UserRole.STAFF, "can_manage_content": True}, 200),
        ({"role": UserRole.STAFF}, 403),
        ({}, 403),
    ],
)
def test_content_rights(
    post: Callable[..., Response],
    make_user: Callable[..., User],
    fields: dict[str, Any],
    status: int,
) -> None:
    assert post("/content", make_user(**fields)).status_code == status


@pytest.mark.parametrize(
    "role, status",
    [(UserRole.ADMIN, 200), (UserRole.STAFF, 403), (UserRole.STUDENT, 403)],
)
def test_admin_only(
    post: Callable[..., Response], make_user: Callable[..., User], role: UserRole, status: int
) -> None:
    assert post("/admin", make_user(role=role)).status_code == status


def test_is_class_staff(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
) -> None:
    owner, assistant, outsider = make_user(), make_user(), make_user()
    school_class, other_class = make_class(), make_class()
    make_class_staff(school_class, owner)
    make_class_staff(school_class, assistant, role=ClassStaffRole.ASSISTANT)
    make_class_staff(other_class, outsider)

    assert is_class_staff(owner.id, school_class.id, db)
    assert is_class_staff(assistant.id, school_class.id, db)
    assert not is_class_staff(outsider.id, school_class.id, db)
