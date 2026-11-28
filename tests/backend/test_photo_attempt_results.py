"""Recording and dropping OCR results (verification/attempts.py: record_result, save_response,
discard; spec Stage 2: Verification, "Retries and concurrency" and "Stale OCR runs"). Integration
tests against the database; the readings are made up, never read by OCR."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.enums import LocationStatus, PhotoAttemptStatus, PhotoResult
from app.models.game import GameSession, SessionLocation
from app.models.verification import PhotoAttempt
from app.verification.attempts import (
    PendingAttempt,
    discard,
    find_replay,
    record_result,
    reserve_attempt,
    save_response,
)
from app.verification.images import PreparedPhoto
from app.verification.matching import Candidate, Decision, SignReading

MakeAttempt = Callable[..., PhotoAttempt]
SHA = sha256(b"the uploaded file").hexdigest()
KEY = "upload-1"
STARTED = datetime(2026, 11, 26, 18, 0, tzinfo=UTC)
MATCH = SignReading(Decision(PhotoResult.MATCH, Candidate("SCI333", 0.92)), "SCI 333", 3600)
UNCLEAR = SignReading(Decision(PhotoResult.UNCLEAR, Candidate("SCI334", 0.7)), "SCI 334", 4100)
NOTHING = SignReading(Decision(PhotoResult.NO_MATCH, None), "", 900)


@pytest.fixture(autouse=True)
def no_autoflush(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """As in the app's sessions, so reading a row back with SQL shows what was flushed."""
    monkeypatch.setattr(db, "autoflush", False)


@pytest.fixture
def game(make_game_session: Callable[..., GameSession]) -> GameSession:
    return make_game_session()


@pytest.fixture
def location(
    game: GameSession, make_session_location: Callable[..., SessionLocation]
) -> SessionLocation:
    return make_session_location(game, 1, status=LocationStatus.VERIFYING)


@pytest.fixture
def running(location: SessionLocation, make_photo_attempt: MakeAttempt) -> PhotoAttempt:
    """Attempt 1 of the location, with a key, while its photo is being read."""
    return make_photo_attempt(location, idempotency_key=KEY, content_sha256=SHA)


def _run(attempt: PhotoAttempt) -> PendingAttempt:
    return PendingAttempt(attempt.id, attempt.job_id, attempt.attempt_no)


def _stored(attempt: PhotoAttempt, column: str, db: Session) -> Any:
    """A column as the database holds it, not as the session remembers it."""
    query = text(f"SELECT {column} FROM photo_attempts WHERE id = :id")
    return db.scalar(query, {"id": attempt.id})


# record_result


@pytest.mark.parametrize(
    ("reading", "candidate", "confidence"),
    [(MATCH, "SCI333", 0.92), (UNCLEAR, "SCI334", 0.7), (NOTHING, None, None)],
    ids=["match", "unclear", "nothing-readable"],
)
def test_a_result_is_written_to_its_attempt(
    db: Session,
    location: SessionLocation,
    running: PhotoAttempt,
    reading: SignReading,
    candidate: str | None,
    confidence: float | None,
) -> None:
    assert record_result(_run(running), reading, db) is running

    assert _stored(running, "status", db) == PhotoAttemptStatus.DONE
    assert _stored(running, "result", db) == reading.decision.result
    assert (running.extracted_candidate, running.ocr_confidence) == (candidate, confidence)
    assert (running.ocr_raw_text, running.processing_ms) == (
        reading.raw_text,
        reading.processing_ms,
    )
    # the response comes after the game has counted the attempt
    assert running.response_json is None
    assert location.attempts_used == 0


@pytest.mark.parametrize(
    ("fields", "run"),
    [
        ({"status": PhotoAttemptStatus.ERROR}, _run),
        ({"status": PhotoAttemptStatus.DONE, "result": PhotoResult.NO_MATCH}, _run),
        ({}, lambda attempt: PendingAttempt(attempt.id, uuid4(), attempt.attempt_no)),
    ],
    ids=["released", "already-written", "another-run"],
)
def test_a_result_is_dropped_once_the_attempt_is_not_the_runs(
    db: Session,
    location: SessionLocation,
    make_photo_attempt: MakeAttempt,
    fields: dict[str, Any],
    run: Callable[[PhotoAttempt], PendingAttempt],
) -> None:
    attempt = make_photo_attempt(location, **fields)
    status, result = attempt.status, attempt.result

    assert record_result(run(attempt), MATCH, db) is None

    assert (_stored(attempt, "status", db), _stored(attempt, "result", db)) == (status, result)
    assert _stored(attempt, "ocr_raw_text", db) is None


@pytest.mark.parametrize(
    ("early", "late"), [(1, False), (0, True)], ids=["1s-before", "at-timeout"]
)
def test_a_result_arriving_after_the_timeout_is_dropped(
    db: Session,
    location: SessionLocation,
    make_photo_attempt: MakeAttempt,
    freeze_clock: Callable[..., None],
    early: int,
    late: bool,
) -> None:
    attempt = make_photo_attempt(location, created_at=STARTED)
    age = get_settings().photo_processing_timeout_seconds - early
    freeze_clock(STARTED + timedelta(seconds=age), "app.verification.attempts")

    recorded = record_result(_run(attempt), MATCH, db)

    assert (recorded is None) == late
    assert _stored(attempt, "status", db) == (
        PhotoAttemptStatus.ERROR if late else PhotoAttemptStatus.DONE
    )


# save_response


def test_a_replay_finds_the_stored_response(
    db: Session, game: GameSession, running: PhotoAttempt
) -> None:
    body = {"result": "match", "attempt": 1, "feedback": [{"code": "VERIFY_SUCCESS"}]}
    recorded = record_result(_run(running), MATCH, db)
    assert recorded is not None

    save_response(recorded, body, db)

    assert _stored(running, "response_json", db) == body
    replay = find_replay(game.id, KEY, SHA, db)
    assert replay is not None and replay.response_json == body


# discard


def test_a_discarded_attempt_frees_its_number_and_key(
    db: Session,
    game: GameSession,
    location: SessionLocation,
    running: PhotoAttempt,
    stored_media_dir: Path,
) -> None:
    discard(_run(running), db)

    assert _stored(running, "status", db) == PhotoAttemptStatus.ERROR
    assert find_replay(game.id, KEY, SHA, db) is None
    photo = PreparedPhoto(b"jpeg", Image.new("L", (1, 1)), has_camera_exif=False)
    assert reserve_attempt(location, KEY, SHA, photo, db).attempt_no == running.attempt_no


@pytest.mark.parametrize(
    ("fields", "run"),
    [
        ({"status": PhotoAttemptStatus.DONE, "result": PhotoResult.MATCH}, _run),
        ({}, lambda attempt: PendingAttempt(attempt.id, uuid4(), attempt.attempt_no)),
    ],
    ids=["written", "another-run"],
)
def test_discard_leaves_an_attempt_the_run_no_longer_owns(
    db: Session,
    location: SessionLocation,
    make_photo_attempt: MakeAttempt,
    fields: dict[str, Any],
    run: Callable[[PhotoAttempt], PendingAttempt],
) -> None:
    attempt = make_photo_attempt(location, **fields)
    status = attempt.status

    discard(run(attempt), db)

    assert _stored(attempt, "status", db) == status


# locks and transactions


@pytest.fixture
def statements(db: Session) -> Iterator[list[str]]:
    """The SQL the session sends, in order."""
    seen: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        seen.append(statement)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", record)
    yield seen
    event.remove(bind, "before_cursor_execute", record)


@pytest.mark.parametrize(
    "finish",
    [lambda run, db: record_result(run, MATCH, db), discard],
    ids=["record_result", "discard"],
)
def test_the_attempt_is_locked_before_it_is_changed(
    db: Session,
    running: PhotoAttempt,
    statements: list[str],
    finish: Callable[[PendingAttempt, Session], object],
) -> None:
    finish(_run(running), db)

    read = next(i for i, sql in enumerate(statements) if sql.startswith("SELECT"))
    write = next(i for i, sql in enumerate(statements) if sql.startswith("UPDATE photo_attempts"))
    assert "FROM photo_attempts" in statements[read] and "FOR UPDATE" in statements[read]
    assert read < write


def test_nothing_commits(
    db: Session,
    location: SessionLocation,
    running: PhotoAttempt,
    make_photo_attempt: MakeAttempt,
    transaction_calls: list[str],
) -> None:
    recorded = record_result(_run(running), MATCH, db)
    assert recorded is not None
    save_response(recorded, {"result": "match"}, db)
    discard(_run(make_photo_attempt(location, attempt_no=2)), db)

    assert set(transaction_calls) == {"flush"}
