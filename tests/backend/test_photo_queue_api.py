"""POST /games/{id}/verification/photo and the OCR queue (A10; spec Stage 2, step 6: at most 2 OCR
jobs at once, 503 OCR_BUSY with Retry-After if 10 are already waiting; OCR_BUSY is no attempt).

`send_photo` sends through `ocr_queue`, a queue with one place, so a test that holds that place
has filled the queue. The fixtures are in verification_fixtures.py; the queue's own limits are
tested in test_ocr_queue.py.
"""

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from httpx2 import Response
from sqlalchemy.orm import Session
from verification_fixtures import AN_HOUR_AGO, attempts_of, location_at

from app.api.v1 import verification as photo_route
from app.config import get_settings
from app.errors import STATUS, AppError, ErrorCode
from app.models.enums import PhotoAttemptStatus, SessionState
from app.models.game import GameSession
from app.verification.ocr_queue import OcrQueue

KEY = "queued-photo-1"


@pytest.mark.parametrize("key", [KEY, None], ids=["key", "no key"])
def test_a_photo_sent_while_the_queue_is_full_is_busy_and_uses_no_attempt(
    db: Session,
    send_photo: Callable[..., Response],
    photo_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
    ocr_queue: OcrQueue,
    assert_error: Callable[[Response, int, ErrorCode], None],
    stored_media_dir: Path,
    key: str | None,
) -> None:
    game = photo_game()
    reads = ocr_reads(("SCI 333", 0.92))

    with ocr_queue.place():  # another upload has the only place
        response = send_photo(game, key=key)

    assert_error(response, 503, ErrorCode.OCR_BUSY)
    assert response.headers["Retry-After"] == "5"
    db.expire_all()
    row = location_at(game, 1, db)
    assert (reads, row.attempts_used, attempts_of(row, db)) == ([], 0, [])
    assert list(stored_media_dir.iterdir()) == []  # no photo saved
    assert game.last_activity_at == AN_HOUR_AGO  # rolled back: not a game action
    assert send_photo(game, key=key).json()["attempt"] == 1  # sent again once there is room


@pytest.mark.parametrize(
    ("game_fields", "data", "code"),
    [
        (
            {"state": SessionState.PAUSED, "paused_at": AN_HOUR_AGO},
            None,
            ErrorCode.SESSION_NOT_ACTIVE,
        ),
        ({}, b"GIF89a" + bytes(64), ErrorCode.UNSUPPORTED_MEDIA_TYPE),
        ({}, b"\xff\xd8\xff not really a photo", ErrorCode.UNSUPPORTED_MEDIA_TYPE),
    ],
    ids=["paused game", "gif", "jpeg that doesn't decode"],
)
@pytest.mark.parametrize("key", [KEY, None], ids=["key", "no key"])
def test_a_refusal_comes_before_a_full_queue(
    send_photo: Callable[..., Response],
    photo_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
    ocr_queue: OcrQueue,
    assert_error: Callable[[Response, int, ErrorCode], None],
    game_fields: dict[str, object],
    data: bytes | None,
    code: ErrorCode,
    key: str | None,
) -> None:
    game = photo_game(**game_fields)
    ocr_reads(("SCI 333", 0.92))

    with ocr_queue.place():
        assert_error(send_photo(game, data, key), STATUS[code], code)


def test_a_repeated_key_is_answered_while_the_queue_is_full(
    send_photo: Callable[..., Response],
    photo_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
    ocr_queue: OcrQueue,
) -> None:
    game = photo_game()
    reads = ocr_reads(("SCI 333", 0.92))
    first = send_photo(game, key=KEY)

    with ocr_queue.place():
        again = send_photo(game, key=KEY)

    assert (again.status_code, again.json(), len(reads)) == (200, first.json(), 1)


def test_the_place_is_given_back_before_the_result_is_counted(
    send_photo: Callable[..., Response],
    photo_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
    ocr_queue: OcrQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game = photo_game()
    ocr_reads(("SCI 333", 0.92))
    apply_photo_result = photo_route.apply_photo_result

    def apply_once_the_place_is_free(*args: Any) -> Any:
        with ocr_queue.place():  # 503 OCR_BUSY if this upload still had the only place
            return apply_photo_result(*args)

    monkeypatch.setattr(photo_route, "apply_photo_result", apply_once_the_place_is_free)

    assert send_photo(game).status_code == 200


@pytest.fixture
def slot_and_waiting_place(monkeypatch: pytest.MonkeyPatch, ocr_queue: OcrQueue) -> OcrQueue:
    """The route's queue with one reading slot and one waiting place (replacing `ocr_queue`), so
    the test and the route can each hold a place while one of them reads."""
    queue = OcrQueue(running=1, waiting=1)
    monkeypatch.setattr(photo_route, "get_ocr_queue", lambda: queue)
    return queue


def test_the_photo_is_read_in_a_reading_slot(
    send_photo: Callable[..., Response],
    photo_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
    slot_and_waiting_place: OcrQueue,
) -> None:
    game = photo_game()
    seen: list[ErrorCode] = []

    def read_another_photo() -> None:  # while this photo is read: is the only slot taken?
        with slot_and_waiting_place.place() as other:
            try:
                other.read(list, 0)
            except AppError as busy:
                seen.append(busy.code)

    ocr_reads(("SCI 333", 0.92), during=read_another_photo)

    assert send_photo(game).status_code == 200
    assert seen == [ErrorCode.OCR_BUSY]


def test_an_upload_still_waiting_at_the_timeout_gives_up_and_uses_no_attempt(
    db: Session,
    send_photo: Callable[..., Response],
    photo_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
    slot_and_waiting_place: OcrQueue,
    assert_error: Callable[[Response, int, ErrorCode], None],
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    monkeypatch.setenv("PHOTO_PROCESSING_TIMEOUT_SECONDS", "1")
    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)  # the next caller reads the restored env
    game = photo_game()
    reads = ocr_reads(("SCI 333", 0.92))

    reading, finished = threading.Event(), threading.Event()

    def read_another_photo() -> None:  # holds the only slot past the timeout, for at most 5 s
        with slot_and_waiting_place.place() as other:
            other.read(lambda: (reading.set(), finished.wait(5)), 5)

    threading.Thread(target=read_another_photo, daemon=True).start()
    assert reading.wait(5)
    response = send_photo(game, key=KEY)
    finished.set()

    assert_error(response, 503, ErrorCode.OCR_BUSY)
    assert response.headers["Retry-After"] == "5" and reads == []
    db.expire_all()
    row = location_at(game, 1, db)
    assert row.attempts_used == 0
    assert [attempt.status for attempt in attempts_of(row, db)] == [PhotoAttemptStatus.ERROR]
