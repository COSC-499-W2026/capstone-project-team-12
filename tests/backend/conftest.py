"""Shared fixtures for backend tests."""

import io
import random
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta, tzinfo
from itertools import count
from pathlib import Path
from typing import Any, NamedTuple, TypeVar
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

import av
import pytest
from fastapi.testclient import TestClient
from jwt import encode
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content.clue_audio import UPLOAD_PROVIDER
from app.db import DB_CONNECT_ARGS, get_db
from app.main import create_app
from app.models.accounts import ClassStaff, User
from app.models.content import AudioAsset, Clue, FeedbackAudio, Location
from app.models.engagement import ACCESS_CODE_ALPHABET, AccessCode, Class, ClassEnrollment
from app.models.enums import (
    ClassStaffRole,
    Difficulty,
    EnrollmentEndReason,
    FeedbackCode,
    Language,
    SessionState,
    UserRole,
)
from app.models.game import GameSession, SessionLocation
from app.services.storage import MEDIA_URL_PREFIX, get_storage_service


@pytest.fixture(scope="session")
def test_engine() -> Iterator[Engine]:
    engine = create_engine(
        get_settings().database_url, pool_pre_ping=True, connect_args=DB_CONNECT_ARGS
    )
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
def transaction_calls(db: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Every explicit commit, flush and rollback made on the test session while the test runs.

    Autoflush is turned off: the automatic flush before a query is normal and only writes what
    the caller already added, so a real lookup that queries must not fail this check. Flush is
    spied on directly because SQLAlchemy only fires its flush events when something is pending,
    so listening for them would miss an explicit flush of a clean session. The transaction is
    opened first because rolling back a session without one does nothing, which would hide a
    rollback inside a lookup.
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
def auth_headers() -> Callable[[User], dict[str, str]]:
    """The Authorization header for a user, with an access token that is valid for an hour."""

    def headers(user: User) -> dict[str, str]:
        expires = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
        key = str(get_settings().access_token_secret_key)
        token = encode({"sub": str(user.id), "exp": expires}, key, "HS256")
        return {"Authorization": f"Bearer {token}"}

    return headers


@pytest.fixture
def leave_as() -> Callable[
    [TestClient, User, str, Callable[[User], dict[str, str]]],
    Any,
]:
    """Shared helper for authenticated leave-class requests."""

    def send(
        client: TestClient,
        user: User,
        class_id: str,
        auth_headers: Callable[[User], dict[str, str]],
    ) -> Any:
        return client.post(
            f"/api/v1/me/classes/{class_id}/leave",
            headers=auth_headers(user),
        )

    return send


@pytest.fixture
def split_media_url() -> Callable[[str], tuple[str, int, str]]:
    """Split a signed media URL into its (key, expires, signature) parts, decoding the key as the
    router does."""

    def split(url: str) -> tuple[str, int, str]:
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        key = unquote(parts.path.removeprefix(f"{MEDIA_URL_PREFIX}/"))
        return key, int(query["expires"][0]), query["sig"][0]

    return split


@pytest.fixture
def freeze_clock(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Make datetime.now() return a fixed time in the given modules, so a test can hit an exact
    time limit, e.g. freeze_clock(now, "app.game.status"). Each module must import `datetime`
    from datetime. Call it again to move the clock.

    `frozen` has to be timezone-aware. A naive value is read as local time, so now(UTC) shifts
    when the machine is not on UTC. Only the modules named here are frozen: one you leave out,
    such as app.game.control, keeps the real clock. now() with no time zone is that instant as a
    naive local time, the same as the real datetime.now().
    """

    def freeze(frozen: datetime, *modules: str) -> None:
        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz: tzinfo | None = None) -> "FrozenDatetime":
                return cls.fromtimestamp(frozen.timestamp(), tz)

        for module in modules:
            monkeypatch.setattr(f"{module}.datetime", FrozenDatetime)

    return freeze


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


class TwoClassStudent(NamedTuple):
    student: User
    japanese: Class
    french: Class
    japanese_enrolment: ClassEnrollment
    french_enrolment: ClassEnrollment


@pytest.fixture
def two_class_student(
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> TwoClassStudent:
    """A student in a Japanese and a French class (joined in that order). The current class is
    the Japanese one and the Practice language is Japanese."""
    joined = datetime(2026, 10, 1, 12, tzinfo=UTC)
    japanese, french = (
        make_class(target_language=Language.JA),
        make_class(target_language=Language.FR),
    )
    student = make_user(current_class_id=japanese.id, preferred_language=Language.JA)
    return TwoClassStudent(
        student,
        japanese,
        french,
        make_enrollment(student, japanese, joined_at=joined),
        make_enrollment(student, french, joined_at=joined + timedelta(days=1)),
    )


@pytest.fixture
def end_enrolment(db: Session) -> Callable[..., None]:
    """Ends an enrolment the way leaving does (left by default)."""

    def end(
        enrolment: ClassEnrollment, reason: EnrollmentEndReason = EnrollmentEndReason.LEFT
    ) -> None:
        enrolment.is_active = False
        enrolment.end_reason = reason
        enrolment.left_at = datetime.now(UTC)
        db.flush()

    return end


@pytest.fixture
def open_game(make_game_session: Callable[..., GameSession]) -> Callable[..., GameSession]:
    """An open game of the student: active, or paused with state=SessionState.PAUSED."""

    def make(
        student: User, state: SessionState = SessionState.ACTIVE, **fields: Any
    ) -> GameSession:
        if state == SessionState.PAUSED:
            fields.setdefault("paused_at", datetime.now(UTC))
        return make_game_session(student, state=state, **fields)

    return make


@pytest.fixture
def make_session_location(db: Session) -> Callable[..., SessionLocation]:
    """A pending location in the game with a valid content snapshot (a random location id)."""

    def make(game: GameSession, position: int, **fields: Any) -> SessionLocation:
        values: dict[str, Any] = {
            "session_id": game.id,
            "location_id": uuid4(),
            "position": position,
            "clue_id": uuid4(),
            "audio_asset_id": uuid4(),
            "accepted_answers_snapshot": ["SCI333"],
            "gps_points_snapshot": [{"lat": 49.9394, "lng": -119.3948}],
            "radius_snapshot": 30,
        }
        return _add(db, SessionLocation(**(values | fields)))

    return make


# Unique test room numbers: "TST 1000", "TST 1001", ... (4 digits match the room number format).
_room_numbers = count(1000)


@pytest.fixture
def make_location(db: Session) -> Callable[..., Location]:
    """An active location with a new room number, its answer and one entrance."""

    def make(**fields: Any) -> Location:
        room_number = f"TST {next(_room_numbers)}"
        values: dict[str, Any] = {
            "name": f"Test room {room_number}",
            "building_name": "Test Building",
            "room_number": room_number,
            "accepted_answers": [room_number.replace(" ", "")],
            "gps_points": [{"lat": 49.9394, "lng": -119.3948}],
            "gps_radius_meters": get_settings().gps_radius_meters,
        }
        return _add(db, Location(**(values | fields)))

    return make


@pytest.fixture
def make_clue(db: Session, make_location: Callable[..., Location]) -> Callable[..., Clue]:
    """A Japanese difficulty 1 clue with no transcript, for a new location unless given one."""

    def make(location: Location | None = None, **fields: Any) -> Clue:
        values: dict[str, Any] = {"difficulty": Difficulty.BASIC, "language": Language.JA}
        if "location_id" not in fields:
            values["location_id"] = (location or make_location()).id
        return _add(db, Clue(**(values | fields)))

    return make


@pytest.fixture
def make_audio_asset(db: Session, make_clue: Callable[..., Clue]) -> Callable[..., AudioAsset]:
    """A 5-second uploaded recording that is not primary, for a new clue unless given one."""

    def make(clue: Clue | None = None, **fields: Any) -> AudioAsset:
        values: dict[str, Any] = {
            "file_path": f"{uuid4()}.mp3",
            "provider": UPLOAD_PROVIDER,
            "duration_seconds": 5.0,
            "file_size_kb": 80,
        }
        if "clue_id" not in fields:
            values["clue_id"] = (clue or make_clue()).id
        return _add(db, AudioAsset(**(values | fields)))

    return make


@pytest.fixture
def make_feedback_audio(db: Session) -> Callable[..., FeedbackAudio]:
    """The phrase `code` in `language` (NAV_START in Japanese by default)."""

    def make(
        code: str = FeedbackCode.NAV_START, language: str = Language.JA, **fields: Any
    ) -> FeedbackAudio:
        values: dict[str, Any] = {"code": code, "language": language, "file_path": f"{uuid4()}.mp3"}
        return _add(db, FeedbackAudio(**(values | fields)))

    return make


# Uploaded recordings for the audio checks: (FFmpeg container format, encoder) per kind of file.
# "adts" is raw AAC, "mp2" MPEG layer II and the "*-in-mp4" kinds FLAC or Opus in MP4: none of them
# is MP3 or M4A. "alac" is an M4A holding lossless audio, which is accepted and re-encoded.
_AUDIO_FILE_FORMATS = {
    "mp3": ("mp3", "libmp3lame"),
    "m4a": ("ipod", "aac"),
    "adts": ("adts", "aac"),
    "alac": ("ipod", "alac"),
    "flac-in-mp4": ("mp4", "flac"),
    "opus-in-mp4": ("mp4", "libopus"),
    "mp2": ("mp2", "mp2"),
}
_SAMPLE_RATE = 48000  # one rate every encoder above accepts (Opus needs 48 kHz)


@pytest.fixture
def make_audio() -> Callable[..., bytes]:
    """A silent mono recording `seconds` long, made with PyAV: make(seconds, kind, title=...),
    `kind` one of _AUDIO_FILE_FORMATS (default "mp3"). A title is written as a tag on the file and
    on the stream, as in a real upload."""

    def make(seconds: float, kind: str = "mp3", title: str | None = None) -> bytes:
        container_format, encoder = _AUDIO_FILE_FORMATS[kind]
        buffer = io.BytesIO()
        with av.open(buffer, "w", format=container_format) as container:
            stream = container.add_stream(encoder, rate=_SAMPLE_RATE, layout="mono")
            if title:
                container.metadata["title"] = title
                stream.metadata["title"] = title
            frame_size = stream.codec_context.frame_size or 1024
            total = round(seconds * _SAMPLE_RATE)
            for start in range(0, total, frame_size):
                frame = av.AudioFrame(
                    format=stream.codec_context.format.name,
                    layout="mono",
                    samples=min(frame_size, total - start),
                )
                for plane in frame.planes:
                    plane.update(bytes(plane.buffer_size))  # silence
                frame.sample_rate, frame.pts = _SAMPLE_RATE, start
                container.mux(stream.encode(frame))
            container.mux(stream.encode(None))
        return buffer.getvalue()

    return make


@pytest.fixture
def stored_media_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An empty folder that StorageService saves into for this test (MEDIA_DIR)."""
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path))
    get_settings.cache_clear()
    get_storage_service.cache_clear()
    yield tmp_path
    # the next caller builds both again from the restored environment
    get_settings.cache_clear()
    get_storage_service.cache_clear()
