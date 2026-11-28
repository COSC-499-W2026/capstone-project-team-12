"""POST /games/{id}/verification/photo over HTTP (A10 TC1 to TC3, the refusals, no answer leaks,
results dropped during OCR).

OCR is replaced by a fake `read_text` that returns fixed text boxes, so no model is loaded; it can
also change the game while it "reads", as another request would between the two transactions.
The game rows are committed before each request, so a refusal's rollback only undoes the request.
"""

import io
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1 import verification as photo_route
from app.config import get_settings
from app.errors import STATUS, ErrorCode
from app.game.control import quit_game
from app.game.shuffle import shuffle_game
from app.models.accounts import User
from app.models.engagement import PointsLedger
from app.models.enums import LocationStatus, PhotoAttemptStatus, SessionState
from app.models.game import GameSession, SessionLocation
from app.models.verification import PhotoAttempt
from app.services.uploads import BYTES_PER_MB
from app.verification import verify
from app.verification.matching import TextBox

AN_HOUR_AGO = datetime.now(UTC) - timedelta(hours=1)
KEY = "photo-" + "1" * 122  # the longest key allowed, 128 characters
RESULT_FIELDS = {"result", "attempt", "attempts_remaining", "feedback"} | {
    "location_result",
    "engagement",
    "next",
    "summary",
}


def jpeg(colour: str = "white", camera: str | None = None) -> bytes:
    """A small JPEG; `camera` is written as its EXIF Make tag."""
    exif = Image.Exif()
    exif.update({} if camera is None else {0x010F: camera})
    data = io.BytesIO()
    Image.new("RGB", (64, 48), colour).save(data, "JPEG", exif=exif)
    return data.getvalue()


@pytest.fixture
def verifying_game(
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
        first = location(game, 1, db)
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
def upload(
    client: TestClient,
    db: Session,
    auth_headers: Callable[[User], dict[str, str]],
    stored_media_dir: Path,
) -> Callable[..., Response]:
    """Sends a photo to a game as its student, with an Idempotency-Key if `key` is given."""

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


def location(game: GameSession, position: int, db: Session) -> SessionLocation:
    return db.scalars(
        select(SessionLocation).where(
            SessionLocation.session_id == game.id, SessionLocation.position == position
        )
    ).one()


def ledger_points(row: SessionLocation, db: Session) -> list[int]:
    awards = db.scalars(select(PointsLedger.points).where(PointsLedger.source_ref == str(row.id)))
    return list(awards)


def attempts_of(row: SessionLocation, db: Session) -> list[PhotoAttempt]:
    query = select(PhotoAttempt).where(PhotoAttempt.session_location_id == row.id)
    return list(db.scalars(query.order_by(PhotoAttempt.created_at)))


def codes(body: dict[str, object]) -> list[str]:
    feedback = body["feedback"]
    assert isinstance(feedback, list)
    assert all(item["audio_url"] for item in feedback)
    return [item["code"] for item in feedback]


def test_a_photo_of_the_right_sign_completes_the_location(
    db: Session,
    upload: Callable[..., Response],
    verifying_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
    stored_media_dir: Path,
) -> None:
    game = verifying_game()
    reads = ocr_reads(("SCI 333", 0.92))

    response = upload(game, jpeg(camera="PhoneMaker"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == RESULT_FIELDS
    assert (body["result"], body["attempt"], body["attempts_remaining"]) == ("match", 1, 5)
    assert codes(body) == ["VERIFY_SUCCESS"]
    assert body["location_result"] == {"status": "completed", "points_awarded": 100}
    assert body["engagement"]["total_points"] == 150  # the location's 100 + the challenge's 50
    assert body["next"]["position"] == 2 and body["next"]["stage"] == "navigation"
    assert body["next"]["clue_audio_url"] and body["summary"] is None
    assert "SCI333" not in response.text and "SCI 333" not in response.text
    assert len(reads) == 1
    db.expire_all()
    first = location(game, 1, db)
    assert (first.status, first.attempts_used) == (LocationStatus.COMPLETED, 1)
    assert ledger_points(first, db) == [100]
    assert (game.total_score, game.current_position) == (100, 2)
    assert game.last_activity_at > AN_HOUR_AGO
    (attempt,) = attempts_of(first, db)
    assert (attempt.status, attempt.result, attempt.attempt_no) == ("done", "match", 1)
    assert attempt.response_json == body and attempt.has_camera_exif
    assert Image.open(stored_media_dir / attempt.photo_path).getexif() == {}


def test_six_photos_of_another_room_fail_the_location(
    db: Session,
    upload: Callable[..., Response],
    verifying_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
    assert_error: Callable[[Response, int, ErrorCode], None],
) -> None:
    game = verifying_game()
    ocr_reads(("LIB 312", 0.95))

    for attempt in range(1, 6):
        body = upload(game).json()
        assert (body["result"], body["attempt"], body["attempts_remaining"]) == (
            "no_match",
            attempt,
            6 - attempt,
        )
        assert codes(body) == ["VERIFY_NO_MATCH"]
        assert body["location_result"] is body["engagement"] is body["next"] is None
        assert body["summary"] is None
    response = upload(game)
    body = response.json()

    assert "LIB312" not in response.text and "LIB 312" not in response.text
    assert (body["result"], body["attempt"], body["attempts_remaining"]) == ("no_match", 6, 0)
    assert codes(body) == ["VERIFY_EXHAUSTED"]
    assert body["location_result"] == {"status": "failed", "points_awarded": 0}
    assert body["engagement"]["total_points"] == 0
    assert body["next"]["position"] == 2 and body["summary"] is None
    db.expire_all()
    assert ledger_points(location(game, 1, db), db) == []
    assert_error(upload(game), 409, ErrorCode.INVALID_STAGE)  # the game has moved on


def test_an_unclear_photo_uses_an_attempt(
    db: Session,
    upload: Callable[..., Response],
    verifying_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
) -> None:
    game = verifying_game()
    ocr_reads(("SCI 334", 0.9))

    body = upload(game).json()

    assert (body["result"], body["attempt"], codes(body)) == ("unclear", 1, ["VERIFY_UNCLEAR"])
    assert body["location_result"] is body["next"] is None
    db.expire_all()
    first = location(game, 1, db)
    assert (first.status, first.attempts_used) == (LocationStatus.VERIFYING, 1)


@pytest.mark.parametrize(
    ("sign", "attempts_used", "phrase", "status", "points"),
    [
        ("SCI 333", 0, "VERIFY_SUCCESS", "completed", 100),
        ("LIB 312", 5, "VERIFY_EXHAUSTED", "failed", 0),
    ],
    ids=["match", "6th miss"],
)
def test_the_last_location_completes_the_game(
    db: Session,
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    upload: Callable[..., Response],
    verifying_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
    sign: str,
    attempts_used: int,
    phrase: str,
    status: str,
    points: int,
) -> None:
    game = verifying_game(locations=1, attempts_used=attempts_used)
    ocr_reads((sign, 0.92))

    body = upload(game).json()

    assert codes(body) == [phrase, "SESSION_COMPLETE"]
    assert body["location_result"] == {"status": status, "points_awarded": points}
    assert body["next"] is None
    assert (body["summary"]["state"], body["summary"]["total_score"]) == ("completed", points)
    student = db.get(User, game.student_id)
    assert student is not None
    summary = client.get(f"/api/v1/games/{game.id}/summary", headers=auth_headers(student))
    assert body["summary"] == summary.json()
    db.expire_all()
    assert game.state == SessionState.COMPLETED


@pytest.mark.parametrize(
    ("data", "status", "code"),
    [
        (b"GIF89a" + bytes(64), 415, ErrorCode.UNSUPPORTED_MEDIA_TYPE),
        (b"\xff\xd8\xff not really a photo", 415, ErrorCode.UNSUPPORTED_MEDIA_TYPE),
        (b"\xff\xd8\xff" + bytes(5 * BYTES_PER_MB), 413, ErrorCode.FILE_TOO_LARGE),
    ],
    ids=["gif", "broken jpeg", "over 5 MB"],
)
def test_a_refused_file_uses_no_attempt(
    db: Session,
    upload: Callable[..., Response],
    verifying_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
    assert_error: Callable[[Response, int, ErrorCode], None],
    data: bytes,
    status: int,
    code: ErrorCode,
) -> None:
    game = verifying_game()
    reads = ocr_reads(("SCI 333", 0.92))

    assert_error(upload(game, data, KEY), status, code)

    assert reads == []
    db.expire_all()
    assert location(game, 1, db).attempts_used == 0
    assert attempts_of(location(game, 1, db), db) == []
    assert game.last_activity_at == AN_HOUR_AGO  # rolled back: not a game action
    assert upload(game, key=KEY).json()["attempt"] == 1  # the key can be sent again


# All 409, checked before the file (without a key), so a GIF gets them too. The 6th attempt fails
# the location, so only a lowered MAX_PHOTO_ATTEMPTS leaves one in Stage 2 with no attempts left.
@pytest.mark.parametrize(
    ("game_fields", "code"),
    [
        ({"status": LocationStatus.NAVIGATING}, ErrorCode.INVALID_STAGE),
        ({"attempts_used": 6}, ErrorCode.ATTEMPTS_EXHAUSTED),
        ({"state": SessionState.PAUSED, "paused_at": AN_HOUR_AGO}, ErrorCode.SESSION_NOT_ACTIVE),
        (
            {"state": SessionState.COMPLETED, "completed_at": AN_HOUR_AGO},
            ErrorCode.SESSION_NOT_ACTIVE,
        ),
    ],
)
def test_a_photo_out_of_turn_is_refused_before_ocr(
    upload: Callable[..., Response],
    verifying_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
    assert_error: Callable[[Response, int, ErrorCode], None],
    game_fields: dict[str, object],
    code: ErrorCode,
) -> None:
    game = verifying_game(**game_fields)
    reads = ocr_reads(("SCI 333", 0.92))

    assert_error(upload(game, b"GIF89a" + bytes(64)), 409, code)

    assert reads == []


@pytest.mark.parametrize(
    ("locations", "sign", "attempts_used"),
    [(2, "SCI 333", 0), (1, "SCI 333", 0), (2, "LIB 312", 5)],
    ids=["match, moved on", "match, game completed", "6th miss"],
)
def test_a_repeated_key_returns_the_first_response_and_uses_no_attempt(
    db: Session,
    upload: Callable[..., Response],
    verifying_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
    assert_error: Callable[[Response, int, ErrorCode], None],
    locations: int,
    sign: str,
    attempts_used: int,
) -> None:
    game = verifying_game(locations=locations, attempts_used=attempts_used)
    reads = ocr_reads((sign, 0.92))
    first = upload(game, key=KEY)
    reads.clear()

    again = upload(game, key=KEY)

    assert (first.status_code, again.status_code, reads) == (200, 200, [])  # no OCR again
    assert again.json() == first.json()
    assert_error(upload(game, jpeg("black"), KEY), 422, ErrorCode.IDEMPOTENCY_KEY_REUSED)
    db.expire_all()
    row = location(game, 1, db)
    assert row.attempts_used == attempts_used + 1 and len(attempts_of(row, db)) == 1
    assert ledger_points(row, db) == ([100] if sign == "SCI 333" else [])


@pytest.mark.parametrize("key", [KEY, "another-photo", None], ids=["same key", "other", "none"])
def test_an_upload_while_a_photo_of_the_location_is_read_is_refused(
    db: Session,
    upload: Callable[..., Response],
    verifying_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
    make_photo_attempt: Callable[..., PhotoAttempt],
    assert_error: Callable[[Response, int, ErrorCode], None],
    key: str | None,
) -> None:
    game = verifying_game()
    reads = ocr_reads(("SCI 333", 0.92))
    file_sha256 = sha256(jpeg()).hexdigest()
    make_photo_attempt(location(game, 1, db), idempotency_key=KEY, content_sha256=file_sha256)
    db.commit()

    response = upload(game, key=key)

    assert_error(response, 409, ErrorCode.UPLOAD_IN_PROGRESS)
    assert response.headers["Retry-After"] == "2" and reads == []


@pytest.mark.parametrize("key", ["", "k" * 129, "two words"], ids=["empty", "too long", "space"])
def test_a_malformed_key_is_refused(
    upload: Callable[..., Response],
    verifying_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
    assert_error: Callable[[Response, int, ErrorCode], None],
    key: str,
) -> None:
    game = verifying_game()
    ocr_reads(("SCI 333", 0.92))

    assert_error(upload(game, key=key), 422, ErrorCode.VALIDATION_ERROR)


def test_the_attempt_is_committed_before_the_photo_is_read(
    db: Session,
    upload: Callable[..., Response],
    verifying_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
    transaction_calls: list[str],
) -> None:
    game = verifying_game()
    row = location(game, 1, db)
    commits = transaction_calls.count("commit")
    seen: list[tuple[int, str]] = []

    def look() -> None:  # as OCR starts: the request's commits so far, and the attempt
        seen.append((transaction_calls.count("commit") - commits, attempts_of(row, db)[0].status))

    ocr_reads(("SCI 333", 0.92), during=look)

    assert upload(game).status_code == 200
    assert seen == [(1, PhotoAttemptStatus.PROCESSING)]


def age_the_attempt(game: GameSession, db: Session) -> None:  # as old as the timeout
    timeout = timedelta(seconds=get_settings().photo_processing_timeout_seconds)
    for attempt in attempts_of(location(game, 1, db), db):
        attempt.created_at -= timeout


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda game, db: shuffle_game(game.student_id, game.id, 0, db), ErrorCode.STATE_CHANGED),
        (lambda game, db: quit_game(game.student_id, game.id, db), ErrorCode.SESSION_NOT_ACTIVE),
        (age_the_attempt, ErrorCode.OCR_BUSY),
    ],
    ids=["shuffled", "quit", "past the timeout"],
)
def test_a_result_is_dropped_when_the_location_changed_during_ocr(
    db: Session,
    upload: Callable[..., Response],
    verifying_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
    assert_error: Callable[[Response, int, ErrorCode], None],
    change: Callable[[GameSession, Session], object],
    code: ErrorCode,
) -> None:
    game = verifying_game()
    row = location(game, 1, db)
    ocr_reads(("SCI 333", 0.92), during=lambda: (change(game, db), db.commit()))

    response = upload(game, key=KEY)

    assert_error(response, STATUS[code], code)
    assert response.headers.get("Retry-After") == ("5" if code == ErrorCode.OCR_BUSY else None)
    db.expire_all()
    assert row.attempts_used == 0 and ledger_points(row, db) == []
    assert [attempt.status for attempt in attempts_of(row, db)] == [PhotoAttemptStatus.ERROR]


def fail(*_args: object) -> None:
    raise RuntimeError("server error")


@pytest.mark.parametrize(
    ("module", "step"),
    [(verify, "read_text"), (photo_route, "save_response")],
    ids=["in OCR", "after counting"],
)
def test_a_server_error_releases_the_attempt(
    db: Session,
    upload: Callable[..., Response],
    verifying_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    step: str,
) -> None:
    game = verifying_game()
    ocr_reads(("SCI 333", 0.92))
    monkeypatch.setattr(module, step, fail)

    with pytest.raises(RuntimeError, match="server error"):
        upload(game, key=KEY)

    db.expire_all()
    row = location(game, 1, db)
    assert (row.attempts_used, ledger_points(row, db)) == (0, [])  # nothing counted is kept
    assert [attempt.status for attempt in attempts_of(row, db)] == [PhotoAttemptStatus.ERROR]
    monkeypatch.undo()
    ocr_reads(("SCI 333", 0.92))
    assert upload(game, key=KEY).json()["attempt"] == 1  # the key can be sent again
