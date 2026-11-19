"""Tests for GET /api/v1/me/profile."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import TwoClassStudent
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy.orm import Session

from app.accounts.password import hash_password
from app.engagement.interface import validate_code_and_enrol
from app.errors import ErrorCode
from app.models.accounts import User
from app.models.engagement import AccessCode, ClassEnrollment
from app.models.enums import EnrollmentEndReason, Language, UserRole

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


def class_ids(body: dict[str, Any]) -> list[str]:
    """The ids of the classes in a profile response, in order."""
    return [item["id"] for item in body["classes"]]


def login(client: TestClient, email: str = VALID_EMAIL, password: str = VALID_PASSWORD) -> Response:
    """POST /auth/login and return the response.

    Side effect: clears the client's cookies afterwards, so the refresh cookie from this login
    doesn't leak into later requests on the shared client.
    """
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


def test_profile_lists_each_class_with_its_name_language_and_join_date(
    db: Session, get_profile: Callable[[User], Response], two_class_student: TwoClassStudent
) -> None:
    roster = two_class_student  # joined Japanese, then French a day later
    roster.french.name = "FREN 101 - Fall 2026"
    db.flush()

    response = get_profile(roster.student)

    assert response.status_code == 200, response.text
    body = response.json()
    classes = body["classes"]
    assert [{k: v for k, v in item.items() if k != "joined_at"} for item in classes] == [
        {"id": str(roster.french.id), "name": "FREN 101 - Fall 2026", "language": "fr"},
        {"id": str(roster.japanese.id), "name": "JAPN 101 - Fall 2026", "language": "ja"},
    ]
    # compared as instants, since the offset in the text depends on the database's time zone
    assert [datetime.fromisoformat(item["joined_at"]) for item in classes] == [
        roster.french_enrolment.joined_at,
        roster.japanese_enrolment.joined_at,
    ]
    assert body["current_class_id"] == str(roster.japanese.id)
    assert body["preferred_language"] == Language.JA.value


def test_profile_keeps_the_first_join_date_of_a_rejoined_class(
    db: Session,
    get_profile: Callable[[User], Response],
    two_class_student: TwoClassStudent,
    end_enrolment: Callable[..., None],
    make_access_code: Callable[..., AccessCode],
) -> None:
    roster = two_class_student  # joined Japanese, then French
    end_enrolment(roster.japanese_enrolment)
    validate_code_and_enrol(roster.student.id, make_access_code(roster.japanese).code, db)

    response = get_profile(roster.student)

    assert response.status_code == 200, response.text
    # rejoining reuses the enrolment, so Japanese still ranks by the date it was first joined
    body = response.json()
    assert class_ids(body) == [str(roster.french.id), str(roster.japanese.id)]
    assert datetime.fromisoformat(body["classes"][1]["joined_at"]) == datetime(
        2026, 10, 1, 12, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "reason",
    [EnrollmentEndReason.LEFT, EnrollmentEndReason.REMOVED, EnrollmentEndReason.ARCHIVED],
)
def test_profile_leaves_out_ended_enrolments(
    get_profile: Callable[[User], Response],
    two_class_student: TwoClassStudent,
    end_enrolment: Callable[[ClassEnrollment, EnrollmentEndReason], None],
    reason: EnrollmentEndReason,
) -> None:
    roster = two_class_student
    end_enrolment(roster.french_enrolment, reason)

    response = get_profile(roster.student)

    assert response.status_code == 200, response.text
    assert class_ids(response.json()) == [str(roster.japanese.id)]


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
    assert class_ids(body) == [str(roster.french.id)]
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
