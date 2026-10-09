"""Shared fixtures for backend tests."""

import random
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.main import create_app
from app.models.accounts import ClassStaff, User
from app.models.engagement import ACCESS_CODE_ALPHABET, AccessCode, Class, ClassEnrollment
from app.models.enums import ClassStaffRole, Difficulty, Language, UserRole
from app.models.game import GameSession


@pytest.fixture(scope="session")
def test_engine() -> Iterator[Engine]:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db(test_engine: Engine) -> Iterator[Session]:
    connection = test_engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db: Session) -> TestClient:
    """API client whose requests use the test session: endpoints see the rows a test made with
    the factories below, and everything they write is rolled back after the test."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


# Factories for the core tables (#8). Each one adds a valid row with unique values and flushes,
# so the row has an id. Pass any column to override a default, e.g. make_user(role=UserRole.STAFF).


T = TypeVar("T")


def _add(db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def make_user(db: Session) -> Callable[..., User]:
    """A student by default."""

    def make(**fields: Any) -> User:
        values: dict[str, Any] = {
            "email": f"{uuid4().hex[:12]}@example.com",
            "username": f"user_{uuid4().hex[:8]}",
            "role": UserRole.STUDENT,
        }
        return _add(db, User(**(values | fields)))

    return make


@pytest.fixture
def make_class(db: Session) -> Callable[..., Class]:
    """A Japanese class in 2026W1 by default, with no staff."""

    def make(**fields: Any) -> Class:
        values: dict[str, Any] = {
            "name": "JAPN 101 - Fall 2026",
            "semester": "2026W1",
            "target_language": Language.JA,
        }
        return _add(db, Class(**(values | fields)))

    return make


@pytest.fixture
def make_class_staff(db: Session) -> Callable[..., ClassStaff]:
    """Makes the user the class owner by default; pass role=ClassStaffRole.ASSISTANT for a TA."""

    def make(school_class: Class, user: User, role: str = ClassStaffRole.OWNER) -> ClassStaff:
        return _add(db, ClassStaff(class_id=school_class.id, user_id=user.id, role=role))

    return make


@pytest.fixture
def make_access_code(db: Session, make_user: Callable[..., User]) -> Callable[..., AccessCode]:
    """An active code for the class, unlimited uses, valid for 90 days, made by a new staff user."""

    def make(school_class: Class, **fields: Any) -> AccessCode:
        values: dict[str, Any] = {
            "class_id": school_class.id,
            "code": "".join(random.choices(ACCESS_CODE_ALPHABET, k=8)),
            "expires_at": datetime.now(UTC) + timedelta(days=90),
        }
        if "created_by" not in fields:
            values["created_by"] = make_user(role=UserRole.STAFF).id
        return _add(db, AccessCode(**(values | fields)))

    return make


@pytest.fixture
def make_enrollment(
    db: Session, make_access_code: Callable[..., AccessCode]
) -> Callable[..., ClassEnrollment]:
    """An active enrolment through a new code for the class, in the class language."""

    def make(student: User, school_class: Class, **fields: Any) -> ClassEnrollment:
        values: dict[str, Any] = {
            "student_id": student.id,
            "class_id": school_class.id,
            "language": school_class.target_language,
        }
        if "access_code_id" not in fields:
            values["access_code_id"] = make_access_code(school_class).id
        return _add(db, ClassEnrollment(**(values | fields)))

    return make


@pytest.fixture
def make_game_session(db: Session, make_user: Callable[..., User]) -> Callable[..., GameSession]:
    """An active Practice game in Japanese at difficulty 1, for a new student unless given one."""

    def make(student: User | None = None, **fields: Any) -> GameSession:
        values: dict[str, Any] = {
            "student_id": (student or make_user()).id,
            "target_language": Language.JA,
            "difficulty": Difficulty.BASIC,
        }
        return _add(db, GameSession(**(values | fields)))

    return make
