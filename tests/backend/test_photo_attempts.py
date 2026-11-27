"""Idempotency-Key replays, stale runs and reserved attempts (verification/attempts.py; spec
Stage 2: Verification, "Retries and concurrency" and "Key scope"). Integration tests against the
database; the photo is a fixed byte string, never read by OCR."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.models.enums import LocationStatus, PhotoAttemptStatus, PhotoResult, SkipReason
from app.models.game import GameSession, SessionLocation
from app.models.verification import PhotoAttempt
from app.verification.attempts import PendingAttempt, find_replay, reserve_attempt
from app.verification.images import PreparedPhoto

MakeAttempt = Callable[..., PhotoAttempt]
SHA = sha256(b"the uploaded file").hexdigest()
OTHER_SHA = sha256(b"another file").hexdigest()
KEY = "upload-1"
DONE: dict[str, Any] = {"status": PhotoAttemptStatus.DONE, "result": PhotoResult.NO_MATCH}
ERROR: dict[str, Any] = {"status": PhotoAttemptStatus.ERROR}
STARTED = datetime(2026, 11, 26, 18, 0, tzinfo=UTC)
COMPLETED = dict(status=LocationStatus.COMPLETED, points_awarded=100, completed_at=STARTED)
PHOTO = PreparedPhoto(
    data=b"re-encoded jpeg", enhanced=Image.new("L", (1, 1)), has_camera_exif=True
)


@pytest.fixture
def game(make_game_session: Callable[..., GameSession]) -> GameSession:
    return make_game_session()


@pytest.fixture
def location(
    game: GameSession, make_session_location: Callable[..., SessionLocation]
) -> SessionLocation:
    return make_session_location(game, 1, status=LocationStatus.VERIFYING)


def _refused(action: Callable[[], object]) -> AppError:
    with pytest.raises(AppError) as refused:
        action()
    return refused.value


def _attempts(location: SessionLocation, db: Session) -> list[PhotoAttempt]:
    return list(
        db.scalars(
            select(PhotoAttempt)
            .where(PhotoAttempt.session_location_id == location.id)
            .order_by(PhotoAttempt.created_at, PhotoAttempt.attempt_no)
        )
    )


# find_replay


@pytest.mark.parametrize(
    "fields",
    [
        {"status": LocationStatus.VERIFYING},
        COMPLETED,
        {"status": LocationStatus.FAILED, "attempts_used": 6},
        {"status": LocationStatus.SKIPPED, "skip_reason": SkipReason.SHUFFLED},
    ],
    ids=["verifying", "completed", "failed", "shuffled"],
)
def test_a_finished_upload_is_replayed_whatever_its_location_became(
    db: Session,
    game: GameSession,
    make_session_location: Callable[..., SessionLocation],
    make_photo_attempt: MakeAttempt,
    fields: dict[str, Any],
) -> None:
    location = make_session_location(game, 1, **fields)
    attempt = make_photo_attempt(
        location, idempotency_key=KEY, content_sha256=SHA, response_json={"attempt": 1}, **DONE
    )

    assert find_replay(game.id, KEY, SHA, db) is attempt
    assert attempt.status == PhotoAttemptStatus.DONE


def test_a_key_the_game_has_not_finished_with_is_a_new_upload(
    db: Session,
    game: GameSession,
    location: SessionLocation,
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    make_photo_attempt: MakeAttempt,
) -> None:
    other_game = make_game_session()
    other_location = make_session_location(other_game, 1, status=LocationStatus.VERIFYING)
    make_photo_attempt(other_location, idempotency_key=KEY, content_sha256=SHA, **DONE)
    make_photo_attempt(location, idempotency_key="another-key", content_sha256=SHA, **DONE)
    failed = make_photo_attempt(
        location, attempt_no=2, idempotency_key=KEY, content_sha256=SHA, **ERROR
    )

    # another game's key, another key of this game, and a run that failed
    assert find_replay(game.id, KEY, SHA, db) is None
    assert failed.status == PhotoAttemptStatus.ERROR


def test_the_newest_attempt_with_the_key_is_replayed(
    db: Session,
    game: GameSession,
    make_session_location: Callable[..., SessionLocation],
    make_photo_attempt: MakeAttempt,
) -> None:
    # the key was used at position 1 and again at position 2
    first = make_session_location(game, 1, **COMPLETED)
    later = make_session_location(game, 2, status=LocationStatus.VERIFYING)
    newest = make_photo_attempt(
        later, idempotency_key=KEY, content_sha256=SHA, created_at=STARTED, **DONE
    )
    make_photo_attempt(
        first,
        idempotency_key=KEY,
        content_sha256=SHA,
        created_at=STARTED - timedelta(minutes=5),
        **DONE,
    )

    assert find_replay(game.id, KEY, SHA, db) is newest


@pytest.mark.parametrize("fields", [DONE, {}], ids=["done", "processing"])
def test_the_key_with_another_file_is_refused(
    db: Session,
    game: GameSession,
    location: SessionLocation,
    make_photo_attempt: MakeAttempt,
    fields: dict[str, Any],
) -> None:
    make_photo_attempt(location, idempotency_key=KEY, content_sha256=SHA, **fields)

    error = _refused(lambda: find_replay(game.id, KEY, OTHER_SHA, db))
    assert error.code == ErrorCode.IDEMPOTENCY_KEY_REUSED


def test_a_key_still_being_read_is_refused_with_retry_after(
    db: Session, game: GameSession, location: SessionLocation, make_photo_attempt: MakeAttempt
) -> None:
    attempt = make_photo_attempt(location, idempotency_key=KEY, content_sha256=SHA)

    error = _refused(lambda: find_replay(game.id, KEY, SHA, db))
    assert error.code == ErrorCode.UPLOAD_IN_PROGRESS
    assert error.headers == {"Retry-After": "2"}
    assert attempt.status == PhotoAttemptStatus.PROCESSING


@pytest.mark.parametrize(
    ("timeout", "age", "stale"),
    [(None, 119, False), (None, 120, True), (30, 29, False), (30, 30, True)],
    ids=["119s", "120s", "29s-of-30", "30s-of-30"],
)
def test_a_run_past_the_timeout_is_released_and_the_upload_goes_on(
    db: Session,
    game: GameSession,
    location: SessionLocation,
    make_photo_attempt: MakeAttempt,
    freeze_clock: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
    timeout: int | None,
    age: int,
    stale: bool,
) -> None:
    if timeout is not None:
        monkeypatch.setattr(get_settings(), "photo_processing_timeout_seconds", timeout)
    # the app's sessions don't autoflush, so the release must be written before returning
    monkeypatch.setattr(db, "autoflush", False)
    attempt = make_photo_attempt(
        location, idempotency_key=KEY, content_sha256=SHA, created_at=STARTED
    )
    freeze_clock(STARTED + timedelta(seconds=age), "app.verification.attempts")

    if stale:
        assert find_replay(game.id, KEY, SHA, db) is None
    else:
        assert _refused(lambda: find_replay(game.id, KEY, SHA, db)).code == (
            ErrorCode.UPLOAD_IN_PROGRESS
        )
    stored = db.scalar(text("SELECT status FROM photo_attempts WHERE id = :id"), {"id": attempt.id})
    assert stored == (PhotoAttemptStatus.ERROR if stale else PhotoAttemptStatus.PROCESSING)


# reserve_attempt


@pytest.mark.parametrize("key", [KEY, None], ids=["key", "no-key"])
def test_a_new_upload_saves_the_photo_and_reserves_the_next_attempt(
    db: Session,
    location: SessionLocation,
    make_photo_attempt: MakeAttempt,
    stored_media_dir: Path,
    key: str | None,
) -> None:
    location.attempts_used = 2
    for attempt_no in (1, 2):
        make_photo_attempt(location, attempt_no=attempt_no, **DONE)

    pending = reserve_attempt(location, key, SHA, PHOTO, db)

    attempt = db.get_one(PhotoAttempt, pending.id)
    assert pending == PendingAttempt(attempt.id, attempt.job_id, 3) and attempt.attempt_no == 3
    assert attempt.status == PhotoAttemptStatus.PROCESSING and attempt.result is None
    assert (attempt.idempotency_key, attempt.content_sha256) == (key, SHA)
    assert attempt.has_camera_exif
    assert attempt.photo_path is not None and attempt.photo_path.endswith(".jpg")
    assert (stored_media_dir / attempt.photo_path).read_bytes() == PHOTO.data


@pytest.mark.parametrize("key", [KEY, None], ids=["same-key", "no-key"])
def test_a_photo_still_being_read_blocks_the_location(
    db: Session,
    location: SessionLocation,
    make_photo_attempt: MakeAttempt,
    stored_media_dir: Path,
    key: str | None,
) -> None:
    make_photo_attempt(location, idempotency_key="first-upload")

    error = _refused(lambda: reserve_attempt(location, key, SHA, PHOTO, db))
    assert error.code == ErrorCode.UPLOAD_IN_PROGRESS
    assert error.headers == {"Retry-After": "2"}
    assert len(_attempts(location, db)) == 1
    assert list(stored_media_dir.iterdir()) == []


def test_a_row_the_database_refuses_leaves_no_photo_file(
    db: Session, location: SessionLocation, stored_media_dir: Path
) -> None:
    # the route checks the key's format; a key that slips through fails before the save
    with pytest.raises(IntegrityError, match="ck_photo_attempts_idempotency_key"):
        reserve_attempt(location, "not a key", SHA, PHOTO, db)
    assert list(stored_media_dir.iterdir()) == []


def test_a_stale_run_frees_the_location_and_its_attempt_number(
    db: Session,
    location: SessionLocation,
    make_photo_attempt: MakeAttempt,
    freeze_clock: Callable[..., None],
    stored_media_dir: Path,
) -> None:
    # a run without a key left behind by a restart, then an upload with a key
    stale = make_photo_attempt(location, created_at=STARTED)
    freeze_clock(STARTED + timedelta(seconds=120), "app.verification.attempts")

    pending = reserve_attempt(location, KEY, SHA, PHOTO, db)

    assert stale.status == PhotoAttemptStatus.ERROR
    assert pending.attempt_no == stale.attempt_no == 1
    assert pending.job_id != stale.job_id


def test_a_failed_run_can_be_retried_with_its_key(
    db: Session, location: SessionLocation, make_photo_attempt: MakeAttempt, stored_media_dir: Path
) -> None:
    failed = make_photo_attempt(location, idempotency_key=KEY, content_sha256=SHA, **ERROR)

    pending = reserve_attempt(location, KEY, SHA, PHOTO, db)

    # the failed row stays as history; the retry is a new row with the same number and key
    statuses = {attempt.id: attempt.status for attempt in _attempts(location, db)}
    assert statuses == {failed.id: ERROR["status"], pending.id: PhotoAttemptStatus.PROCESSING}
    assert pending.attempt_no == 1


def test_a_photo_of_a_shuffled_out_location_does_not_block_the_new_one(
    db: Session,
    game: GameSession,
    location: SessionLocation,
    make_session_location: Callable[..., SessionLocation],
    make_photo_attempt: MakeAttempt,
    stored_media_dir: Path,
) -> None:
    # shuffled while its photo was being read; the new location took its position
    make_photo_attempt(location, idempotency_key=KEY)
    location.status, location.skip_reason = LocationStatus.SKIPPED, SkipReason.SHUFFLED
    db.flush()
    replacement = make_session_location(game, 1, status=LocationStatus.VERIFYING)

    pending = reserve_attempt(replacement, KEY, SHA, PHOTO, db)

    assert pending.attempt_no == 1


def test_neither_function_commits(
    db: Session,
    game: GameSession,
    location: SessionLocation,
    stored_media_dir: Path,
    transaction_calls: list[str],
) -> None:
    pending = reserve_attempt(location, KEY, SHA, PHOTO, db)
    with pytest.raises(AppError):
        find_replay(game.id, KEY, SHA, db)

    assert set(transaction_calls) == {"flush"}
    assert db.get_one(PhotoAttempt, pending.id).status == PhotoAttemptStatus.PROCESSING
