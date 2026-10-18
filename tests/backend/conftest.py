"""Shared fixtures for backend tests."""

import random
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.main import create_app
from app.models.accounts import ClassStaff, User
from app.models.engagement import ACCESS_CODE_ALPHABET, AccessCode, Class, ClassEnrollment
from app.models.enums import ClassStaffRole, Difficulty, Language, UserRole
from app.models.game import GameSession, SessionLocation
from app.services.storage import MEDIA_URL_PREFIX


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
def transaction_calls(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[str]]:
    """Record explicit commits, flushes and rollbacks on the test session.

    Autoflush is disabled because automatic flushing before queries is normal.
    Explicit flushes are tracked directly so even clean-session flushes are
    recorded. The transaction is opened before monitoring begins.
    """
    db.connection()
    calls: list[str] = []
    real_flush = db.flush

    def spy_flush(*args: Any, **kwargs: Any) -> None:
        calls.append("flush")
        real_flush(*args, **kwargs)

    def record(name: str) -> Callable[..., None]:
        return lambda *_: calls.append(name)

    monkeypatch.setattr(db, "autoflush", False)
    monkeypatch.setattr(db, "flush", spy_flush)
    event.listen(db, "after_commit", record("commit"))
    event.listen(db, "after_soft_rollback", record("rollback"))
    yield calls


@pytest.fixture
def split_media_url() -> Callable[[str], tuple[str, int, str]]:
    """Split a signed media URL into its key, expiration and signature."""

    def split(url: str) -> tuple[str, int, str]:
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        key = unquote(parts.path.removeprefix(f"{MEDIA_URL_PREFIX}/"))
        return key, int(query["expires"][0]), query["sig"][0]

    return split


@pytest.fixture
def client(db: Session) -> TestClient:
    """Create an API client using the test database session.

    Endpoint writes and factory-created rows share the same session.
    Changes are rolled back after each test.
    """
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


# Factories for the core tables (#8).
# Each factory inserts a valid row with unique values and flushes.
# Pass model fields as keyword arguments to override defaults.


T = TypeVar("T")


def _add(db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def make_user(db: Session) -> Callable[..., User]:
    """Create a student by default."""

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
    """Create a Japanese class in 2026W1 with no staff by default."""

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
    """Assign a user as class owner, or as a TA when requested."""

    def make(
        school_class: Class,
        user: User,
        role: str = ClassStaffRole.OWNER,
    ) -> ClassStaff:
        return _add(
            db,
            ClassStaff(
                class_id=school_class.id,
                user_id=user.id,
                role=role,
            ),
        )

    return make


@pytest.fixture
def make_access_code(
    db: Session,
    make_user: Callable[..., User],
) -> Callable[..., AccessCode]:
    """Create an active access code valid for 90 days by default."""

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
    db: Session,
    make_access_code: Callable[..., AccessCode],
) -> Callable[..., ClassEnrollment]:
    """Create an enrollment with valid joining and leaving timestamps.

    Active enrollments use the database-generated joining timestamp.

    For already-ended enrollments, set joined_at before left_at
    to satisfy the database's chronological constraint.

    Explicitly supplied joined_at values are preserved.
    """

    def make(
        student: User,
        school_class: Class,
        **fields: Any,
    ) -> ClassEnrollment:
        values: dict[str, Any] = {
            "student_id": student.id,
            "class_id": school_class.id,
            "language": school_class.target_language,
        }

        if "access_code_id" not in fields:
            values["access_code_id"] = make_access_code(school_class).id

        if fields.get("left_at") is not None and "joined_at" not in fields:
            values["joined_at"] = fields["left_at"] - timedelta(seconds=1)

        return _add(
            db,
            ClassEnrollment(**(values | fields)),
        )

    return make


@pytest.fixture
def make_game_session(
    db: Session,
    make_user: Callable[..., User],
) -> Callable[..., GameSession]:
    """Create an active Practice game in Japanese at basic difficulty."""

    def make(
        student: User | None = None,
        **fields: Any,
    ) -> GameSession:
        values: dict[str, Any] = {
            "student_id": (student or make_user()).id,
            "target_language": Language.JA,
            "difficulty": Difficulty.BASIC,
        }

        return _add(
            db,
            GameSession(**(values | fields)),
        )

    return make


@pytest.fixture
def make_session_location(
    db: Session,
) -> Callable[..., SessionLocation]:
    """Create a pending game location with a valid content snapshot."""

    def make(
        game: GameSession,
        position: int,
        **fields: Any,
    ) -> SessionLocation:
        values: dict[str, Any] = {
            "session_id": game.id,
            "location_id": uuid4(),
            "position": position,
            "clue_id": uuid4(),
            "audio_asset_id": uuid4(),
            "accepted_answers_snapshot": ["SCI333"],
            "gps_points_snapshot": [
                {
                    "lat": 49.9394,
                    "lng": -119.3948,
                }
            ],
            "radius_snapshot": 30,
        }

        return _add(
            db,
            SessionLocation(**(values | fields)),
        )

    return make
