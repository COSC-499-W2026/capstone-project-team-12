"""Tests for GET /api/v1/me/profile."""

from collections.abc import Callable

import pytest
from conftest import TwoClassStudent
from fastapi.testclient import TestClient
from httpx2 import Response

from app.accounts.password import hash_password
from app.errors import ErrorCode
from app.models.accounts import User
from app.models.enums import Language, UserRole

URL = "/api/v1/me/profile"
VALID_EMAIL = "test@email.com"
VALID_PASSWORD = "ValidPassword1"


@pytest.fixture
def student(make_user: Callable[..., User]) -> User:
    """An active student who can sign in with VALID_EMAIL and VALID_PASSWORD."""
    return make_user(email=VALID_EMAIL, password_hash=hash_password(VALID_PASSWORD))


@pytest.fixture
def get_profile(
    client: TestClient, auth_headers: Callable[[User], dict[str, str]]
) -> Callable[[User], Response]:
    """GET /me/profile as the user."""

    def get(user: User) -> Response:
        return client.get(URL, headers=auth_headers(user))

    return get


def login(client: TestClient, email: str = VALID_EMAIL, password: str = VALID_PASSWORD) -> Response:
    response = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    client.cookies.clear()  # tests send the refresh cookie themselves, so none leaks between calls
    return response


def test_profile_with_a_token_from_login(client: TestClient, student: User) -> None:
    access_token = login(client).json()["access_token"]

    response = client.get(URL, headers={"Authorization": f"Bearer {access_token}"})

    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(student.id)


def test_profile_of_a_new_student(get_profile: Callable[[User], Response], student: User) -> None:
    response = get_profile(student)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "id": str(student.id),
        "username": student.username,
        "email": VALID_EMAIL,
        "role": UserRole.STUDENT.value,
        "classes": [],
        "current_class_id": None,
        "preferred_language": None,
    }


def test_profile_lists_classes_most_recently_joined_first(
    get_profile: Callable[[User], Response], two_class_student: TwoClassStudent
) -> None:
    roster = two_class_student

    response = get_profile(roster.student)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["classes"] == [str(roster.french.id), str(roster.japanese.id)]
    assert body["current_class_id"] == str(roster.japanese.id)
    assert body["preferred_language"] == Language.JA.value


def test_profile_leaves_out_ended_enrolments(
    get_profile: Callable[[User], Response],
    two_class_student: TwoClassStudent,
    end_enrolment: Callable[..., None],
) -> None:
    roster = two_class_student
    end_enrolment(roster.french_enrolment)

    response = get_profile(roster.student)

    assert response.status_code == 200, response.text
    assert response.json()["classes"] == [str(roster.japanese.id)]


def test_profile_falls_back_when_the_current_class_ended(
    get_profile: Callable[[User], Response],
    two_class_student: TwoClassStudent,
    end_enrolment: Callable[..., None],
) -> None:
    roster = two_class_student
    end_enrolment(roster.japanese_enrolment)

    response = get_profile(roster.student)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["classes"] == [str(roster.french.id)]
    assert body["current_class_id"] == str(roster.french.id)


@pytest.mark.parametrize("role", [UserRole.STAFF, UserRole.ADMIN])
def test_profile_of_staff_and_admins_has_no_classes(
    get_profile: Callable[[User], Response], make_user: Callable[..., User], role: UserRole
) -> None:
    response = get_profile(make_user(role=role))

    assert response.status_code == 200, response.text
    assert response.json()["role"] == role.value
    assert response.json()["classes"] == []


def test_profile_needs_a_signed_in_user(
    client: TestClient, assert_error: Callable[[Response, int, ErrorCode], None]
) -> None:
    assert_error(client.get(URL), 401, ErrorCode.NOT_AUTHENTICATED)


def test_profile_refuses_an_invalid_token(
    client: TestClient, assert_error: Callable[[Response, int, ErrorCode], None]
) -> None:
    response = client.get(URL, headers={"Authorization": "Bearer not-a-token"})

    assert_error(response, 401, ErrorCode.NOT_AUTHENTICATED)
