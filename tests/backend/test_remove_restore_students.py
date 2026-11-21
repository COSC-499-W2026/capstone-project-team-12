"""Owner-only student removal and restoration."""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from conftest import TwoClassStudent
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.errors import ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import AccessCode, Class, ClassEnrollment
from app.models.enums import ClassStaffRole, EnrollmentEndReason, SessionEndReason, SessionState
from app.models.game import GameSession


def test_remove_blocks_join_and_restore_reuses_enrolment(
    client: TestClient,
    db: Session,
    two_class_student: TwoClassStudent,
    make_user: Callable[..., User],
    make_class_staff: Callable[..., ClassStaff],
    make_game_session: Callable[..., GameSession],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student, school_class, fallback, enrolment, _ = two_class_student
    owner = make_user(role="staff")
    make_class_staff(school_class, owner)
    owner_headers = auth_headers(owner)
    student_headers = auth_headers(student)
    game = make_game_session(student, class_id=school_class.id, total_score=100)
    classmate_game = make_game_session(class_id=school_class.id)
    code = db.get_one(AccessCode, enrolment.access_code_id)
    joined_at = enrolment.joined_at
    path = f"/api/v1/teacher/classes/{school_class.id}/students/{student.id}"

    for _ in range(2):
        response = client.delete(path, headers=owner_headers)
        assert response.status_code == 204, response.text
    db.refresh(enrolment)
    db.refresh(student)
    db.refresh(game)
    db.refresh(classmate_game)
    assert not enrolment.is_active
    assert enrolment.end_reason == EnrollmentEndReason.REMOVED
    assert enrolment.left_at is not None
    assert student.current_class_id == fallback.id
    assert game.state == SessionState.ABANDONED
    assert game.end_reason == SessionEndReason.CLASS_CLOSED
    assert game.total_score == 100
    assert classmate_game.state == SessionState.ACTIVE

    response = client.post(
        "/api/v1/me/classes/join", json={"access_code": code.code}, headers=student_headers
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == ErrorCode.REMOVED_FROM_CLASS

    for _ in range(2):
        response = client.post(f"{path}/restore", headers=owner_headers)
        assert response.status_code == 204, response.text
    retry = client.post(
        "/api/v1/me/classes/join", json={"access_code": code.code}, headers=student_headers
    )
    assert retry.status_code == 200, retry.text
    db.refresh(enrolment)
    db.refresh(code)
    db.refresh(student)
    assert enrolment.is_active
    assert enrolment.joined_at == joined_at
    assert enrolment.access_code_id == code.id
    assert enrolment.left_at is None
    assert enrolment.end_reason is None
    assert code.uses_count == 0
    assert student.current_class_id == fallback.id


@pytest.mark.parametrize("is_assistant", [True, False])
def test_only_owner_can_remove_or_restore(
    client: TestClient,
    two_class_student: TwoClassStudent,
    make_user: Callable[..., User],
    make_class_staff: Callable[..., ClassStaff],
    auth_headers: Callable[[User], dict[str, str]],
    is_assistant: bool,
) -> None:
    student, school_class, *_ = two_class_student
    teacher = make_user(role="staff")
    if is_assistant:
        make_class_staff(school_class, teacher, role=ClassStaffRole.ASSISTANT)
    headers = auth_headers(teacher)
    path = f"/api/v1/teacher/classes/{school_class.id}/students/{student.id}"

    for response in (
        client.delete(path, headers=headers),
        client.post(f"{path}/restore", headers=headers),
    ):
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE


def test_restore_rejects_another_active_class_in_the_same_language(
    client: TestClient,
    db: Session,
    two_class_student: TwoClassStudent,
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    make_user: Callable[..., User],
    make_class_staff: Callable[..., ClassStaff],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student, school_class, _, enrolment, _ = two_class_student
    owner = make_user(role="staff")
    make_class_staff(school_class, owner)
    headers = auth_headers(owner)
    path = f"/api/v1/teacher/classes/{school_class.id}/students/{student.id}"
    assert client.delete(path, headers=headers).status_code == 204
    make_enrollment(student, make_class(target_language=school_class.target_language))

    response = client.post(f"{path}/restore", headers=headers)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == ErrorCode.ALREADY_IN_CLASS_FOR_LANGUAGE
    db.refresh(enrolment)
    assert enrolment.end_reason == EnrollmentEndReason.REMOVED


def test_archived_classes_cannot_be_modified(
    client: TestClient,
    db: Session,
    two_class_student: TwoClassStudent,
    make_user: Callable[..., User],
    make_class_staff: Callable[..., ClassStaff],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student, school_class, *_ = two_class_student
    owner = make_user(role="staff")
    make_class_staff(school_class, owner)
    school_class.archived_at = datetime.now(UTC)
    db.commit()
    headers = auth_headers(owner)
    path = f"/api/v1/teacher/classes/{school_class.id}/students/{student.id}"

    for response in (
        client.delete(path, headers=headers),
        client.post(f"{path}/restore", headers=headers),
    ):
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == ErrorCode.CLASS_ARCHIVED
