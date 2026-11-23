"""Concurrent join requests with independent PostgreSQL transactions."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from random import SystemRandom
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, delete, event, select
from sqlalchemy.engine import Result
from sqlalchemy.orm import ORMExecuteState, Session

from app.db import get_db
from app.errors import ErrorCode
from app.main import create_app
from app.models.accounts import ClassStaff, User
from app.models.engagement import ACCESS_CODE_ALPHABET, AccessCode, Class, ClassEnrollment
from app.models.enums import ClassStaffRole, EnrollmentEndReason, Language, UserRole


@pytest.fixture
def join_records(test_engine: Engine) -> Iterator[tuple[list[User], User, list[AccessCode]]]:
    """Commit students and codes so both requests can see the same starting state."""
    with Session(test_engine, expire_on_commit=False) as db:
        users = [
            User(email=f"{uuid4().hex}@example.com", username=f"join_{uuid4().hex[:12]}", role=role)
            for role in (UserRole.STUDENT, UserRole.STUDENT, UserRole.STAFF)
        ]
        classes = [
            Class(name=f"Japanese {number}", semester="2026W1", target_language=Language.JA)
            for number in (1, 2)
        ]
        db.add_all([*users, *classes])
        db.flush()
        random = SystemRandom()
        codes = [
            AccessCode(
                class_id=school_class.id,
                code="".join(random.choices(ACCESS_CODE_ALPHABET, k=8)),
                expires_at=datetime.now(UTC) + timedelta(days=1),
                created_by=users[2].id,
                max_uses=1,
            )
            for school_class in classes
        ]
        db.add_all(codes)
        db.add(ClassStaff(class_id=classes[0].id, user_id=users[2].id, role=ClassStaffRole.OWNER))
        db.commit()
    class_ids = [school_class.id for school_class in classes]
    user_ids = [user.id for user in users]

    try:
        yield users[:2], users[2], codes
    finally:
        with Session(test_engine) as db:
            db.execute(delete(ClassEnrollment).where(ClassEnrollment.class_id.in_(class_ids)))
            db.execute(delete(AccessCode).where(AccessCode.class_id.in_(class_ids)))
            db.execute(delete(ClassStaff).where(ClassStaff.class_id.in_(class_ids)))
            db.execute(delete(User).where(User.id.in_(user_ids)))
            db.execute(delete(Class).where(Class.id.in_(class_ids)))
            db.commit()


@pytest.mark.parametrize(
    ("same_student", "expected_error", "error_status"),
    [
        pytest.param(
            True, ErrorCode.ALREADY_IN_CLASS_FOR_LANGUAGE, 409, id="one-student-two-classes"
        ),
        pytest.param(False, ErrorCode.INVALID_ACCESS_CODE, 400, id="two-students-single-use-code"),
    ],
)
def test_concurrent_joins_commit_only_one_enrolment(
    test_engine: Engine,
    join_records: tuple[list[User], User, list[AccessCode]],
    auth_headers: Callable[[User], dict[str, str]],
    run_together: Callable[..., list[Response]],
    same_student: bool,
    expected_error: ErrorCode,
    error_status: int,
) -> None:
    students, _, codes = join_records
    attempts = [
        (students[0], codes[0]),
        (students[0], codes[1]) if same_student else (students[1], codes[0]),
    ]

    def request(student: User, code: AccessCode) -> Callable[[Session], Response]:
        def join(db: Session) -> Response:
            app = create_app()
            app.dependency_overrides[get_db] = lambda: db
            with TestClient(app) as client:
                return client.post(
                    "/api/v1/me/classes/join",
                    json={"access_code": code.code},
                    headers=auth_headers(student),
                )

        return join

    responses = run_together(*(request(student, code) for student, code in attempts))
    assert sorted(response.status_code for response in responses) == [200, error_status], [
        response.text for response in responses
    ]
    winner = next(index for index, response in enumerate(responses) if response.status_code == 200)
    assert responses[1 - winner].json()["error"]["code"] == expected_error.value
    student, code = attempts[winner]
    assert responses[winner].json()["class_id"] == str(code.class_id)

    with Session(test_engine) as db:
        enrolment = db.scalars(
            select(ClassEnrollment).where(
                ClassEnrollment.student_id.in_([student.id for student in students])
            )
        ).one()
        assert enrolment.is_active
        assert enrolment.student_id == student.id
        assert enrolment.class_id == code.class_id
        assert enrolment.access_code_id == code.id
        uses = db.execute(
            select(AccessCode.id, AccessCode.uses_count).where(
                AccessCode.id.in_([candidate.id for candidate in codes])
            )
        )
        assert {code_id: count for code_id, count in uses} == {
            candidate.id: int(candidate.id == code.id) for candidate in codes
        }


def test_archive_retries_when_a_student_joins_after_the_roster_read(
    test_engine: Engine,
    join_records: tuple[list[User], User, list[AccessCode]],
    auth_headers: Callable[[User], dict[str, str]],
    run_together: Callable[..., list[Response]],
) -> None:
    students, owner, codes = join_records
    student, code = students[0], codes[0]
    roster_read = Event()
    join_finished = Event()
    roster_reads = 0

    def archive(db: Session) -> Response:
        @event.listens_for(db, "do_orm_execute")
        def wait_for_join_after_roster_read(state: ORMExecuteState) -> Result[tuple[User]] | None:
            nonlocal roster_reads
            mapper = state.bind_mapper
            if not (db.in_nested_transaction() and state.is_select and mapper is User.__mapper__):
                return None
            result = state.invoke_statement()
            roster_reads += 1
            if roster_reads == 1:
                # The roster query has run, but archive has yet to lock the class.
                roster_read.set()
                assert join_finished.wait(timeout=5), "Join did not finish after the roster read"
            return result

        app = create_app()
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as client:
            return client.post(
                f"/api/v1/teacher/classes/{code.class_id}/archive", headers=auth_headers(owner)
            )

    def join(db: Session) -> Response:
        assert roster_read.wait(timeout=5), "Archive did not read the roster"
        try:
            app = create_app()
            app.dependency_overrides[get_db] = lambda: db
            with TestClient(app) as client:
                return client.post(
                    "/api/v1/me/classes/join",
                    json={"access_code": code.code},
                    headers=auth_headers(student),
                )
        finally:
            join_finished.set()

    archive_response, join_response = run_together(archive, join)
    assert join_response.status_code == 200, join_response.text
    assert archive_response.status_code == 204, archive_response.text
    assert roster_reads == 2

    with Session(test_engine) as db:
        school_class = db.get_one(Class, code.class_id)
        enrolment = db.scalars(
            select(ClassEnrollment).where(
                ClassEnrollment.class_id == code.class_id,
                ClassEnrollment.student_id == student.id,
            )
        ).one()
        assert school_class.archived_at is not None
        assert not enrolment.is_active
        assert enrolment.end_reason == EnrollmentEndReason.ARCHIVED
        assert enrolment.left_at == school_class.archived_at
        assert db.get_one(User, student.id).current_class_id is None
