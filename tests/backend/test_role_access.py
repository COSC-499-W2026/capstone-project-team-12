from time import time
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
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
from app.errors import add_error_handlers
from app.models.accounts import ClassStaff, User
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, UserRole

PREFIX = "/authtest"
KEY = get_settings().access_token_secret_key
ALGORITHM = "HS256"

test_app = FastAPI(title="Role Access Test App")
add_error_handlers(test_app)
test_router = APIRouter(prefix=PREFIX)
test_app.include_router(test_router)


@pytest.fixture
def test_client(db: Session) -> TestClient:
    test_app.dependency_overrides[get_db] = lambda: db
    return TestClient(test_app)


#  TEST ROUTES
@test_router.post("/restricted", tags=["test"], status_code=200)
def needs_login(user: Annotated[User, Depends(get_current_user)]) -> None:
    return


@test_router.post("/assistant/{class_id}", tags=["test"], status_code=200)
def assistant_access(
    require_assistant: Annotated[None, Depends(require_class_role(ClassStaffRole.ASSISTANT))],
) -> None:
    return


@test_router.post("/teacher/{class_id}", tags=["test"], status_code=200)
def teacher_access(
    require_teacher: Annotated[None, Depends(require_class_role(ClassStaffRole.OWNER))],
) -> None:
    return


@test_router.post("/content", tags=["test"], status_code=200)
def content_manager(
    require_content_manager: Annotated[None, Depends(can_manage_content)],
) -> None:
    return


@test_router.post("/admin", tags=["test"], status_code=200)
def admin(require_admin: Annotated[None, Depends(require_admin)]) -> None:
    return


def test_expired_token(test_client: TestClient, make_user: User):
    user = make_user()
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) - (60 * 60)}, str(KEY), ALGORITHM)
    test_client.headers = {"Authorization": f"Bearer {jwt}"}
    response = test_client.post(f"{PREFIX}/restricted")
    assert response.status_code == 401


def test_different_key(test_client: TestClient, make_user: User):
    user = make_user()
    incorrect_key = "INCORRECT"
    jwt = encode(
        {"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(incorrect_key), ALGORITHM
    )
    test_client.headers = {"Authorization": f"Bearer {jwt}"}
    response = test_client.post(f"{PREFIX}/restricted")
    assert response.status_code == 401


def test_garbage_token(test_client: TestClient):
    jwt = encode({"id": "junky token"}, str(KEY), ALGORITHM)
    test_client.headers = {"Authorization": f"Bearer {jwt}"}
    response = test_client.post(f"{PREFIX}/restricted")
    assert response.status_code == 401


def test_non_uuid(test_client: TestClient):
    jwt = encode({"sub": "NotAUUID", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    test_client.headers = {"Authorization": f"Bearer {jwt}"}
    response = test_client.post(f"{PREFIX}/restricted")
    assert response.status_code == 401


def test_inactive_user(test_client: TestClient, make_user: User):
    user = make_user(is_active=False)
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    test_client.headers = {"Authorization": f"Bearer {jwt}"}
    response = test_client.post(f"{PREFIX}/restricted")
    assert response.status_code == 403


def test_student_cannot_access_class(test_client: TestClient, make_class: Class, make_user: User):
    test_class = make_class()
    user = make_user()
    class_id = test_class.id
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    test_client.headers = {"Authorization": f"Bearer {jwt}"}
    response = test_client.post(f"{PREFIX}/assistant/{class_id}")
    assert response.status_code == 403


def test_assistant_access(
    test_client: TestClient, make_class: Class, make_user: User, make_class_staff: ClassStaff
):
    test_class = make_class()
    user = make_user()
    make_class_staff(school_class=test_class, user=user, role=ClassStaffRole.ASSISTANT)
    class_id = test_class.id
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    test_client.headers = {"Authorization": f"Bearer {jwt}"}
    response = test_client.post(f"{PREFIX}/assistant/{class_id}")
    assert response.status_code == 200


def test_assistant_cannot_access_teacher(
    test_client: TestClient, make_class: Class, make_user: User, make_class_staff: ClassStaff
):
    test_class = make_class()
    user = make_user()
    make_class_staff(school_class=test_class, user=user, role=ClassStaffRole.ASSISTANT)
    class_id = test_class.id
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    test_client.headers = {"Authorization": f"Bearer {jwt}"}
    response = test_client.post(f"{PREFIX}/teacher/{class_id}")
    assert response.status_code == 403


def test_teacher_access(
    test_client: TestClient, make_class: Class, make_user: User, make_class_staff: ClassStaff
):
    test_class = make_class()
    user = make_user()
    make_class_staff(school_class=test_class, user=user, role=ClassStaffRole.OWNER)
    class_id = test_class.id
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    test_client.headers = {"Authorization": f"Bearer {jwt}"}
    response = test_client.post(f"{PREFIX}/teacher/{class_id}")
    assert response.status_code == 200


def test_teacher_cannot_access_another_class(
    test_client: TestClient, make_class: Class, make_user: User, make_class_staff: ClassStaff
):
    other_test_class = make_class()
    accessed_test_class = make_class()
    user = make_user()
    make_class_staff(school_class=other_test_class, user=user, role=ClassStaffRole.OWNER)
    accessed_class_id = accessed_test_class.id
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    test_client.headers = {"Authorization": f"Bearer {jwt}"}
    response = test_client.post(f"{PREFIX}/teacher/{accessed_class_id}")
    assert response.status_code == 403


def test_can_manage_content(test_client: TestClient, make_user: User):
    user = make_user(role=UserRole.STAFF, can_manage_content=True)
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    test_client.headers = {"Authorization": f"Bearer {jwt}"}
    response = test_client.post(f"{PREFIX}/content")
    assert response.status_code == 200


def test_cannot_manage_content(test_client: TestClient, make_user: User):
    user = make_user()
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    test_client.headers = {"Authorization": f"Bearer {jwt}"}
    response = test_client.post(f"{PREFIX}/content")
    assert response.status_code == 403


def test_admin(test_client: TestClient, make_user: User):
    user = make_user(role=UserRole.ADMIN)
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    test_client.headers = {"Authorization": f"Bearer {jwt}"}
    response = test_client.post(f"{PREFIX}/admin")
    assert response.status_code == 200


def test_not_admin(test_client: TestClient, make_user: User):
    user_1 = make_user()  #  Student by default
    user_2 = make_user(role=UserRole.STAFF)
    jwt_1 = encode({"sub": f"{user_1.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    test_client.headers = {"Authorization": f"Bearer {jwt_1}"}
    response_1 = test_client.post(f"{PREFIX}/admin")

    jwt_2 = encode({"sub": f"{user_2.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    test_client.headers = {"Authorization": f"Bearer {jwt_2}"}
    response_2 = test_client.post(f"{PREFIX}/admin")

    assert response_1.status_code == 403 and response_2.status_code == 403


def test_inactive_admin(test_client: TestClient, make_user: User):
    user = make_user(role=UserRole.ADMIN, is_active=False)
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    test_client.headers = {"Authorization": f"Bearer {jwt}"}
    response = test_client.post(f"{PREFIX}/admin")
    assert response.status_code == 403


def test_is_class_staff(
    make_user: User, make_class: Class, make_class_staff: ClassStaff, db: Session
):
    assistant = make_user()
    teacher = make_user()
    test_class = make_class()
    make_class_staff(school_class=test_class, user=assistant, role=ClassStaffRole.ASSISTANT)
    make_class_staff(school_class=test_class, user=teacher, role=ClassStaffRole.OWNER)
    assert is_class_staff(assistant.id, test_class.id, db) and is_class_staff(
        teacher.id, test_class.id, db
    )


def test_not_class_staff(make_user: User, make_class: Class, db: Session):
    user = make_user()
    test_class = make_class()
    assert not is_class_staff(user.id, test_class.id, db)


def test_different_class_staff(
    make_user: User, make_class: Class, make_class_staff: ClassStaff, db: Session
):
    teacher_1 = make_user()
    test_class_1 = make_class()
    make_class_staff(school_class=test_class_1, user=teacher_1)
    teacher_2 = make_user()
    test_class_2 = make_class()
    make_class_staff(school_class=test_class_2, user=teacher_2)
    assert not is_class_staff(teacher_1.id, test_class_2.id, db) and not is_class_staff(
        teacher_2.id, test_class_1.id, db
    )
