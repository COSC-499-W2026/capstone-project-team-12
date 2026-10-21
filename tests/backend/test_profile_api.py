"""Tests for PATCH /api/v1/me/profile (A15)."""

from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest
from conftest import TwoClassStudent
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.errors import ErrorCode
from app.models.accounts import User
from app.models.engagement import Class, ClassEnrollment
from app.models.enums import Language, SessionState, UserRole
from app.models.game import GameSession

URL = "/api/v1/me/profile"


@pytest.fixture
def patch_profile(
    client: TestClient, auth_headers: Callable[[User], dict[str, str]]
) -> Callable[..., Any]:
    """PATCH /me/profile as the user, with the given JSON body."""

    def patch(user: User, body: dict[str, Any]) -> Any:
        return client.patch(URL, json=body, headers=auth_headers(user))

    return patch


def assert_error(response: Any, status: int, code: ErrorCode) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code.value


def test_change_class_and_language(
    db: Session, patch_profile: Callable[..., Any], two_class_student: TwoClassStudent
) -> None:
    roster = two_class_student

    response = patch_profile(
        roster.student, {"current_class_id": str(roster.french.id), "preferred_language": "zh"}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "id": str(roster.student.id),
        "username": roster.student.username,
        "role": UserRole.STUDENT.value,
        "current_class_id": str(roster.french.id),
        "preferred_language": Language.ZH.value,
    }
    db.refresh(roster.student)
    assert roster.student.current_class_id == roster.french.id
    assert roster.student.preferred_language == Language.ZH


def test_null_class_switches_to_practice_and_back(
    db: Session, patch_profile: Callable[..., Any], two_class_student: TwoClassStudent
) -> None:
    roster = two_class_student

    response = patch_profile(roster.student, {"current_class_id": None})

    assert response.status_code == 200, response.text
    assert response.json()["current_class_id"] is None
    db.refresh(roster.student)
    assert roster.student.current_class_id is None

    back = patch_profile(roster.student, {"current_class_id": str(roster.french.id)})
    assert back.json()["current_class_id"] == str(roster.french.id)


def test_class_the_student_is_not_in_is_not_found(
    db: Session,
    patch_profile: Callable[..., Any],
    two_class_student: TwoClassStudent,
    make_class: Callable[..., Class],
    end_enrolment: Callable[..., None],
) -> None:
    roster = two_class_student
    end_enrolment(roster.french_enrolment)

    for class_id in (make_class().id, uuid4(), roster.french.id):
        response = patch_profile(
            roster.student, {"current_class_id": str(class_id), "preferred_language": "zh"}
        )
        assert_error(response, 404, ErrorCode.NOT_FOUND)

    db.refresh(roster.student)
    assert roster.student.current_class_id == roster.japanese.id
    assert roster.student.preferred_language == Language.JA


@pytest.mark.parametrize("state", [SessionState.ACTIVE, SessionState.PAUSED])
def test_open_game_blocks_changes_but_not_repeats(
    db: Session,
    patch_profile: Callable[..., Any],
    two_class_student: TwoClassStudent,
    open_game: Callable[..., GameSession],
    state: SessionState,
) -> None:
    roster = two_class_student
    game = open_game(roster.student, state)

    for body in (
        {"current_class_id": str(roster.french.id)},
        {"current_class_id": None},
        {"preferred_language": "ko"},
    ):
        response = patch_profile(roster.student, body)
        assert_error(response, 409, ErrorCode.SESSION_ALREADY_OPEN)
        assert response.json()["error"]["details"] == [{"session_id": str(game.id)}]
    db.refresh(roster.student)
    assert roster.student.current_class_id == roster.japanese.id

    same = patch_profile(
        roster.student, {"current_class_id": str(roster.japanese.id), "preferred_language": "ja"}
    )
    assert same.status_code == 200, same.text


def test_response_shows_the_class_the_student_still_has(
    patch_profile: Callable[..., Any],
    two_class_student: TwoClassStudent,
    end_enrolment: Callable[..., None],
) -> None:
    roster = two_class_student
    end_enrolment(roster.japanese_enrolment)  # the stored class is no longer active

    response = patch_profile(roster.student, {"preferred_language": "ja"})

    assert response.status_code == 200, response.text
    assert response.json()["current_class_id"] == str(roster.french.id)


def test_student_with_no_practice_language_can_select_a_class(
    db: Session,
    patch_profile: Callable[..., Any],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    # a new student has no Practice language yet
    school_class, student = make_class(), make_user()
    make_enrollment(student, school_class)

    response = patch_profile(student, {"current_class_id": str(school_class.id)})

    assert response.status_code == 200, response.text
    assert response.json()["preferred_language"] is None
    assert response.json()["current_class_id"] == str(school_class.id)
    db.refresh(student)
    assert student.current_class_id == school_class.id


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"preferred_language": None},
        {"preferred_language": "en"},
        {"current_class_id": "not-a-uuid"},
    ],
)
def test_invalid_body(
    patch_profile: Callable[..., Any], make_user: Callable[..., User], body: dict[str, Any]
) -> None:
    assert_error(patch_profile(make_user(), body), 422, ErrorCode.VALIDATION_ERROR)


def test_needs_a_signed_in_user(client: TestClient) -> None:
    response = client.patch(URL, json={"preferred_language": "ko"})

    assert_error(response, 401, ErrorCode.NOT_AUTHENTICATED)


def test_staff_can_set_a_practice_language_but_has_no_class_to_select(
    patch_profile: Callable[..., Any],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
) -> None:
    teacher = make_user(role=UserRole.STAFF)

    ok = patch_profile(teacher, {"preferred_language": "fr"})
    no_class = patch_profile(teacher, {"current_class_id": str(make_class().id)})

    assert ok.status_code == 200, ok.text
    assert ok.json()["preferred_language"] == Language.FR.value
    assert_error(no_class, 404, ErrorCode.NOT_FOUND)


def test_admin_is_forbidden(
    patch_profile: Callable[..., Any], make_user: Callable[..., User]
) -> None:
    response = patch_profile(make_user(role=UserRole.ADMIN), {"preferred_language": "fr"})

    assert_error(response, 403, ErrorCode.FORBIDDEN_ROLE)
