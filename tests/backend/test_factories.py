"""The shared fixtures in conftest.py make valid rows and give the API client the test session."""

from collections.abc import Callable
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.accounts import ClassStaff, User
from app.models.engagement import AccessCode, Class, ClassEnrollment
from app.models.enums import ClassStaffRole, Language, UserRole


def test_each_user_is_a_new_student(make_user: Callable[..., User]) -> None:
    first, second = make_user(), make_user()
    assert first.role == second.role == UserRole.STUDENT
    assert first.email != second.email
    assert first.username != second.username


def test_defaults_can_be_overridden(
    make_user: Callable[..., User], make_class: Callable[..., Class]
) -> None:
    teacher = make_user(role=UserRole.STAFF, can_manage_content=True)
    school_class = make_class(target_language=Language.FR)
    assert teacher.can_manage_content is True
    assert school_class.target_language == Language.FR


def test_class_staff_is_the_owner_by_default(
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
) -> None:
    staff = make_class_staff(make_class(), make_user(role=UserRole.STAFF))
    assert staff.role == ClassStaffRole.OWNER


def test_an_enrolment_uses_a_code_from_its_own_class(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    school_class = make_class(target_language=Language.KO)
    enrollment = make_enrollment(make_user(), school_class)
    code = db.get(AccessCode, enrollment.access_code_id)
    assert code is not None and code.class_id == school_class.id
    assert enrollment.language == Language.KO
    assert enrollment.is_active is True


def test_passing_a_code_or_creator_adds_no_extra_rows(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_access_code: Callable[..., AccessCode],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    def count(model: type[User] | type[AccessCode]) -> int | None:
        return db.scalar(select(func.count()).select_from(model))

    teacher, student, school_class = make_user(role=UserRole.STAFF), make_user(), make_class()
    users, codes = count(User), count(AccessCode)
    code = make_access_code(school_class, created_by=teacher.id)
    make_enrollment(student, school_class, access_code_id=code.id)
    assert (count(User), count(AccessCode)) == (users, codes + 1)


def test_the_client_sees_rows_made_in_the_test(
    client: TestClient, make_user: Callable[..., User]
) -> None:
    """No endpoint reads the database yet, so the test adds one to check the client's session."""
    app = cast(FastAPI, client.app)

    @app.get("/test-only/users/{user_id}")
    def read_user(user_id: UUID, db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
        user = db.get(User, user_id)
        return {"username": user.username if user else ""}

    user = make_user()
    assert client.get(f"/test-only/users/{user.id}").json() == {"username": user.username}
