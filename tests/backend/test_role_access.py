import os

from fastapi.testclient import TestClient
from jwt import encode

from app.models.accounts import ClassStaff, User
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, UserRole

PREFIX = "/api/v1/auth/test"
ACCESS_TOKEN_KEY = os.getenv("ACCESS_TOKEN_KEY")
ALGORITHM = "HS256"


def test_student_cannot_access_class(client: TestClient, make_class: Class, make_user: User):
    test_class = make_class()
    user = make_user()
    class_id = test_class.id
    jwt = encode({"sub": f"{user.id}"}, str(ACCESS_TOKEN_KEY), ALGORITHM)
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
    jwt = encode({"sub": f"{user.id}"}, str(ACCESS_TOKEN_KEY), ALGORITHM)
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
    jwt = encode({"sub": f"{user.id}"}, str(ACCESS_TOKEN_KEY), ALGORITHM)
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
    jwt = encode({"sub": f"{user.id}"}, str(ACCESS_TOKEN_KEY), ALGORITHM)
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
    jwt = encode({"sub": f"{user.id}"}, str(ACCESS_TOKEN_KEY), ALGORITHM)
    client.headers = {"Authorization": f"Bearer {jwt}"}
    response = client.post(f"{PREFIX}/teacher/{accessed_class_id}")
    assert response.status_code == 403


def test_can_manage_content(client: TestClient, make_user: User):
    user = make_user(role=UserRole.STAFF, can_manage_content=True)
    jwt = encode({"sub": f"{user.id}"}, str(ACCESS_TOKEN_KEY), ALGORITHM)
    client.headers = {"Authorization": f"Bearer {jwt}"}
    response = client.post(f"{PREFIX}/content")
    assert response.status_code == 200


def test_cannot_manage_content(client: TestClient, make_user: User):
    user = make_user()
    jwt = encode({"sub": f"{user.id}"}, str(ACCESS_TOKEN_KEY), ALGORITHM)
    client.headers = {"Authorization": f"Bearer {jwt}"}
    response = client.post(f"{PREFIX}/content")
    assert response.status_code == 403
