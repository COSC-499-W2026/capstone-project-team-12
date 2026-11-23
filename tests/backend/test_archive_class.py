"""Owner-only archival closes class enrolments and games."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import TwoClassStudent
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import AccessCode, Class, ClassEnrollment
from app.models.enums import (
    ClassStaffRole,
    EnrollmentEndReason,
    Language,
    SessionEndReason,
    SessionState,
    UserRole,
)
from app.models.game import GameSession


def test_archive_closes_the_class(
    client: TestClient,
    db: Session,
    two_class_student: TwoClassStudent,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    make_access_code: Callable[..., AccessCode],
    make_enrollment: Callable[..., ClassEnrollment],
    make_game_session: Callable[..., GameSession],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student, school_class, fallback, enrolment, fallback_enrolment = two_class_student
    older_class = make_class(target_language=Language.ES)
    make_enrollment(
        student, older_class, joined_at=fallback_enrolment.joined_at - timedelta(days=1)
    )
    archived_class = make_class(target_language=Language.DE, archived_at=datetime.now(UTC))
    make_enrollment(
        student, archived_class, joined_at=fallback_enrolment.joined_at + timedelta(days=1)
    )
    owner = make_user(role=UserRole.STAFF)
    make_class_staff(school_class, owner)
    other_student = make_user(current_class_id=school_class.id)
    other_enrolment = make_enrollment(other_student, school_class)
    game = make_game_session(student, class_id=school_class.id, total_score=100)
    paused = make_game_session(
        other_student,
        class_id=school_class.id,
        state=SessionState.PAUSED,
        paused_at=datetime.now(UTC),
    )
    code = make_access_code(school_class)
    path = f"/api/v1/teacher/classes/{school_class.id}/archive"

    response = client.post(path, headers=auth_headers(owner))
    assert response.status_code == 204, response.text
    db.refresh(school_class)
    archived_at = school_class.archived_at
    assert archived_at is not None
    assert client.post(path, headers=auth_headers(owner)).status_code == 204
    db.refresh(school_class)
    assert school_class.archived_at == archived_at

    for record in (enrolment, other_enrolment):
        db.refresh(record)
        assert not record.is_active
        assert record.left_at == archived_at
        assert record.end_reason == EnrollmentEndReason.ARCHIVED
    for session in (game, paused):
        db.refresh(session)
        assert session.state == SessionState.ABANDONED
        assert session.end_reason == SessionEndReason.CLASS_CLOSED
    assert game.total_score == 100
    db.refresh(student)
    db.refresh(other_student)
    assert student.current_class_id == fallback.id
    assert other_student.current_class_id is None

    response = client.post(
        "/api/v1/games",
        json={"class_id": str(school_class.id), "difficulty": 1},
        headers=auth_headers(student),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == ErrorCode.CLASS_ARCHIVED

    response = client.post(
        "/api/v1/me/classes/join", json={"access_code": code.code}, headers=auth_headers(student)
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == ErrorCode.CLASS_ARCHIVED
    db.refresh(code)
    db.refresh(student)
    assert code.uses_count == 0
    assert student.current_class_id == fallback.id

    replacement = make_class()
    fresh_code = make_access_code(replacement)
    response = client.post(
        "/api/v1/me/classes/join",
        json={"access_code": fresh_code.code},
        headers=auth_headers(student),
    )
    assert response.status_code == 200, response.text
    assert response.json()["class_id"] == str(replacement.id)
    assert (
        db.scalar(
            select(ClassEnrollment.class_id).where(
                ClassEnrollment.student_id == student.id,
                ClassEnrollment.language == school_class.target_language,
                ClassEnrollment.is_active.is_(True),
            )
        )
        == replacement.id
    )


@pytest.mark.parametrize("is_assistant", [True, False])
def test_only_the_owner_can_archive(
    client: TestClient,
    db: Session,
    make_class: Callable[..., Class],
    make_user: Callable[..., User],
    make_class_staff: Callable[..., ClassStaff],
    auth_headers: Callable[[User], dict[str, str]],
    is_assistant: bool,
) -> None:
    school_class = make_class()
    teacher = make_user(role=UserRole.STAFF)
    if is_assistant:
        make_class_staff(school_class, teacher, role=ClassStaffRole.ASSISTANT)

    response = client.post(
        f"/api/v1/teacher/classes/{school_class.id}/archive", headers=auth_headers(teacher)
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN_ROLE
    db.refresh(school_class)
    assert school_class.archived_at is None


def test_archive_unknown_class(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    response = client.post(
        f"/api/v1/teacher/classes/{uuid4()}/archive",
        headers=auth_headers(make_user(role=UserRole.STAFF)),
    )
    assert response.status_code == 404, response.text
