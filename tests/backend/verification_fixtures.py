"""Fixtures for the photo verification tests: the photo_attempts factory, a game waiting for its
sign photo, a fake OCR reader, the route's OCR queue and the photo upload. Registered in
conftest.py through `pytest_plugins`, next to content_fixtures, because conftest.py is already
over 500 lines.

`jpeg`, `location_at` and `attempts_of` are plain helpers; a test module imports them from here.
"""

import io
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1 import verification as photo_route
from app.models.accounts import User
from app.models.enums import LocationStatus
from app.models.game import GameSession, SessionLocation
from app.models.verification import PhotoAttempt
from app.verification import verify
from app.verification.matching import TextBox
from app.verification.ocr_queue import OcrQueue

# the real clock when the tests start (not a frozen NOW): `photo_game`'s last game action
AN_HOUR_AGO = datetime.now(UTC) - timedelta(hours=1)


def jpeg(colour: str = "white", camera: str | None = None) -> bytes:
    """A small JPEG; `camera` is written as its EXIF Make tag."""
    exif = Image.Exif()
    exif.update({} if camera is None else {0x010F: camera})
    data = io.BytesIO()
    Image.new("RGB", (64, 48), colour).save(data, "JPEG", exif=exif)
    return data.getvalue()


def location_at(game: GameSession, position: int, db: Session) -> SessionLocation:
    return db.scalars(
        select(SessionLocation).where(
            SessionLocation.session_id == game.id, SessionLocation.position == position
        )
    ).one()


def attempts_of(row: SessionLocation, db: Session) -> list[PhotoAttempt]:
    query = select(PhotoAttempt).where(PhotoAttempt.session_location_id == row.id)
    return list(db.scalars(query.order_by(PhotoAttempt.created_at)))


@pytest.fixture
def make_photo_attempt(db: Session) -> Callable[..., PhotoAttempt]:
    """Attempt 1 of the session location, still `processing`, for a new file without camera EXIF.

    Pass any column to override a default, e.g. status=PhotoAttemptStatus.DONE with a result.
    """

    def make(location: SessionLocation, **fields: Any) -> PhotoAttempt:
        values: dict[str, Any] = {
            "session_location_id": location.id,
            "attempt_no": 1,
            "photo_path": f"{uuid4().hex}.jpg",
            "content_sha256": sha256(uuid4().bytes).hexdigest(),
            "job_id": uuid4(),
            "has_camera_exif": False,
        }
        attempt = PhotoAttempt(**(values | fields))
        db.add(attempt)
        db.flush()
        return attempt

    return make


@pytest.fixture
def photo_game(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    playable_location_ids: Callable[..., list[UUID]],
    fixed_daily_challenge_targets: None,
) -> Callable[..., GameSession]:
    """A saved game with `locations` playable locations, at the first one, which is in Stage
    2 (or `status`) with the answer SCI333; the others are pending. The last game action was an
    hour ago. Other keywords are for the game."""

    def make(
        locations: int = 2,
        status: LocationStatus = LocationStatus.VERIFYING,
        attempts_used: int = 0,
        **fields: object,
    ) -> GameSession:
        game = make_game_session(make_user(), last_activity_at=AN_HOUR_AGO, **fields)
        active = playable_location_ids(count=locations)
        for position in range(1, locations + 1):
            make_session_location(game, position, location_id=active[position - 1])
        first = location_at(game, 1, db)
        first.status, first.started_at = status, AN_HOUR_AGO
        first.attempts_used = attempts_used
        if status == LocationStatus.VERIFYING:
            first.arrived_at = AN_HOUR_AGO
        db.commit()
        return game

    return make


@pytest.fixture
def ocr_reads(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[int]]:
    """Makes every OCR read return these boxes, after calling `during` (to change the game while
    the photo is read); returns a list that grows by one per read."""

    def read_as(*boxes: tuple[str, float], during: Callable[[], object] = list) -> list[int]:
        reads: list[int] = []

        def read_text(_image: Image.Image) -> list[TextBox]:
            reads.append(1)
            during()
            return [TextBox(text, confidence) for text, confidence in boxes]

        monkeypatch.setattr(verify, "read_text", read_text)
        return reads

    return read_as


@pytest.fixture
def ocr_queue(monkeypatch: pytest.MonkeyPatch) -> OcrQueue:
    """The route's OCR queue for this test, new and with one place: one photo read at a time and
    none waiting. So an upload that kept its place after it ended makes the test's next upload
    503 OCR_BUSY. A test that sends photos at the same moment needs a bigger one."""
    queue = OcrQueue(running=1, waiting=0)
    monkeypatch.setattr(photo_route, "get_ocr_queue", lambda: queue)
    return queue


@pytest.fixture
def send_photo(
    client: TestClient,
    db: Session,
    auth_headers: Callable[[User], dict[str, str]],
    stored_media_dir: Path,
    ocr_queue: OcrQueue,
) -> Callable[..., Response]:
    """Sends a photo to a game as its student, with an Idempotency-Key if `key` is given,
    through the one-place `ocr_queue`."""

    def send(game: GameSession, data: bytes | None = None, key: str | None = None) -> Response:
        student = db.get(User, game.student_id)
        assert student is not None
        key_header = {} if key is None else {"Idempotency-Key": key}
        return client.post(
            f"/api/v1/games/{game.id}/verification/photo",
            files={"photo": ("sign.jpg", jpeg() if data is None else data, "image/jpeg")},
            headers=auth_headers(student) | key_header,
        )

    return send
