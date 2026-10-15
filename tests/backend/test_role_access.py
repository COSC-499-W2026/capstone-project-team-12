from time import time

from fastapi.testclient import TestClient
from jwt import encode
from sqlalchemy.orm import Session

from app.accounts.interface import is_class_staff
from app.config import get_settings
from app.models.accounts import ClassStaff, User
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, UserRole

PREFIX = "/api/v1/auth/test"
KEY = get_settings().secret_key
ALGORITHM = "HS256"


def test_expired_token(client: TestClient, make_user: User):
    user = make_user()
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) - (60 * 60)}, str(KEY), ALGORITHM)
    client.headers = {"Authorization": f"Bearer {jwt}"}
    response = client.post(f"{PREFIX}/restricted")
    assert response.status_code == 401


def test_different_key(client: TestClient, make_user: User):
    user = make_user()
    incorrect_key = "INCORRECT"
    jwt = encode(
        {"sub": f"{user.id}", "exp": int(time()) - (60 * 60)}, str(incorrect_key), ALGORITHM
    )
    client.header = {"Authorization": f"Bearer {jwt}"}
    response = client.post(f"{PREFIX}/restricted")
    assert response.status_code == 401


def test_garbage_token(client: TestClient):
    jwt = encode({"id": "junky token"}, str(KEY), ALGORITHM)
    client.headers = {"Authorization": f"Bearer {jwt}"}
    response = client.post(f"{PREFIX}/restricted")
    assert response.status_code == 401

    def test_non_uuid(client: TestClient):
        jwt = encode({"sub": "NotAUUID"}, str(KEY), ALGORITHM)
        client.headers = {"Authorization": f"Bearer {jwt}"}
        response = client.post(f"{PREFIX}/restricted")
        assert response.status_code == 401


def test_inactive_user(client: TestClient, make_user: User):
    user = make_user(is_active=False)
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    client.headers = {"Authorization": f"Bearer {jwt}"}
    response = client.post(f"{PREFIX}/restricted")
    assert response.status_code == 403


def test_student_cannot_access_class(client: TestClient, make_class: Class, make_user: User):
    test_class = make_class()
    user = make_user()
    class_id = test_class.id
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    client.headers = {"Authorization": f"Bearer {jwt}"}
    response = client.post(f"{PREFIX}/assistant/{class_id}")
    assert response.status_code == 403


def test_assistant_access(
    client: TestClient, make_class: Class, make_user: User, make_class_staff: ClassStaff
):
    test_class = make_class()
    user = make_user()
    make_class_staff(school_class=test_class, user=user, role=ClassStaffRole.ASSISTANT)
    class_id = test_class.id
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    client.headers = {"Authorization": f"Bearer {jwt}"}
    response = client.post(f"{PREFIX}/assistant/{class_id}")
    assert response.status_code == 200


def test_assistant_cannot_access_teacher(
    client: TestClient, make_class: Class, make_user: User, make_class_staff: ClassStaff
):
    test_class = make_class()
    user = make_user()
    make_class_staff(school_class=test_class, user=user, role=ClassStaffRole.ASSISTANT)
    class_id = test_class.id
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    client.headers = {"Authorization": f"Bearer {jwt}"}
    response = client.post(f"{PREFIX}/teacher/{class_id}")
    assert response.status_code == 403


def test_teacher_access(
    client: TestClient, make_class: Class, make_user: User, make_class_staff: ClassStaff
):
    test_class = make_class()
    user = make_user()
    make_class_staff(school_class=test_class, user=user, role=ClassStaffRole.OWNER)
    class_id = test_class.id
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    client.headers = {"Authorization": f"Bearer {jwt}"}
    response = client.post(f"{PREFIX}/teacher/{class_id}")
    assert response.status_code == 200


def test_teacher_cannot_access_another_class(
    client: TestClient, make_class: Class, make_user: User, make_class_staff: ClassStaff
):
    other_test_class = make_class()
    accessed_test_class = make_class()
    user = make_user()
    make_class_staff(school_class=other_test_class, user=user, role=ClassStaffRole.OWNER)
    accessed_class_id = accessed_test_class.id
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    client.headers = {"Authorization": f"Bearer {jwt}"}
    response = client.post(f"{PREFIX}/teacher/{accessed_class_id}")
    assert response.status_code == 403


def test_can_manage_content(client: TestClient, make_user: User):
    user = make_user(role=UserRole.STAFF, can_manage_content=True)
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    client.headers = {"Authorization": f"Bearer {jwt}"}
    response = client.post(f"{PREFIX}/content")
    assert response.status_code == 200


def test_cannot_manage_content(client: TestClient, make_user: User):
    user = make_user()
    jwt = encode({"sub": f"{user.id}", "exp": int(time()) + (60 * 60)}, str(KEY), ALGORITHM)
    client.headers = {"Authorization": f"Bearer {jwt}"}
    response = client.post(f"{PREFIX}/content")
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
