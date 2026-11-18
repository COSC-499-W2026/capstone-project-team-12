"""POST /games/{id}/verification/photo over HTTP (A10 TC1 and TC2, the refusals, no answer leaks).

The access checks (401, another student's 404, an admin's 403) are shared with every game route
through `play` and `lock_photo_location`; their route tests come in a follow-up.

OCR is replaced by a fake `read_text` that returns fixed text boxes, so no model is loaded. The
game rows are committed before each request, so a refusal's rollback only undoes the request.
"""

import io
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content import interface as content
from app.errors import ErrorCode
from app.models.accounts import User
from app.models.engagement import PointsLedger
from app.models.enums import Difficulty, Language, LocationStatus, SessionState
from app.models.game import GameSession, SessionLocation
from app.services.uploads import BYTES_PER_MB
from app.verification import verify
from app.verification.matching import TextBox

AN_HOUR_AGO = datetime.now(UTC) - timedelta(hours=1)
RESULT_FIELDS = {"result", "attempt", "attempts_remaining", "feedback"} | {
    "location_result",
    "engagement",
    "next",
    "summary",
}


def jpeg() -> bytes:
    data = io.BytesIO()
    Image.new("RGB", (64, 48), "white").save(data, "JPEG")
    return data.getvalue()


@pytest.fixture
def verifying_game(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> Callable[..., GameSession]:
    """A saved game with `locations` active (stub) locations, at the first one, which is in Stage
    2 (or `status`) with the answer SCI333; the others are pending. The last game action was an
    hour ago. Other keywords are for the game."""

    def make(
        locations: int = 2,
        status: LocationStatus = LocationStatus.VERIFYING,
        attempts_used: int = 0,
        **fields: object,
    ) -> GameSession:
        game = make_game_session(make_user(), last_activity_at=AN_HOUR_AGO, **fields)
        active = sorted(content.eligible_location_ids(Language.JA, Difficulty.BASIC, db))
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
    """Makes every OCR read return these boxes; returns a list that grows by one per read."""

    def read_as(*boxes: tuple[str, float]) -> list[int]:
        reads: list[int] = []

        def read_text(_image: Image.Image) -> list[TextBox]:
            reads.append(1)
            return [TextBox(text, confidence) for text, confidence in boxes]

        monkeypatch.setattr(verify, "read_text", read_text)
        return reads

    return read_as


@pytest.fixture
def upload(
    client: TestClient, db: Session, auth_headers: Callable[[User], dict[str, str]]
) -> Callable[..., Response]:
    """Sends a photo to a game as its student (or `user`)."""

    def send(game: GameSession, data: bytes | None = None, user: User | None = None) -> Response:
        student = user or db.get(User, game.student_id)
        assert student is not None
        return client.post(
            f"/api/v1/games/{game.id}/verification/photo",
            files={"photo": ("sign.jpg", jpeg() if data is None else data, "image/jpeg")},
            headers=auth_headers(student),
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
) -> None:
    game = verifying_game()
    reads = ocr_reads(("SCI 333", 0.92))

    response = upload(game)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == RESULT_FIELDS
    assert (body["result"], body["attempt"], body["attempts_remaining"]) == ("match", 1, 5)
    assert codes(body) == ["VERIFY_SUCCESS"]
    assert body["location_result"] == {"status": "completed", "points_awarded": 100}
    assert body["engagement"]["total_points"] == 100
    assert body["next"]["position"] == 2 and body["next"]["stage"] == "navigation"
    assert body["next"]["clue_audio_url"] and body["summary"] is None
    assert "SCI333" not in response.text and "SCI 333" not in response.text  # rule 1
    assert len(reads) == 1
    db.expire_all()
    first = location(game, 1, db)
    assert (first.status, first.attempts_used) == (LocationStatus.COMPLETED, 1)
    assert ledger_points(first, db) == [100]
    assert (game.total_score, game.current_position) == (100, 2)
    assert game.last_activity_at > AN_HOUR_AGO


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
    body = upload(game).json()

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


def test_the_last_location_completes_the_game(
    db: Session,
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    upload: Callable[..., Response],
    verifying_game: Callable[..., GameSession],
    ocr_reads: Callable[..., list[int]],
) -> None:
    game = verifying_game(locations=1)
    ocr_reads(("SCI 333", 0.92))

    body = upload(game).json()

    assert codes(body) == ["VERIFY_SUCCESS", "SESSION_COMPLETE"]
    assert body["location_result"] == {"status": "completed", "points_awarded": 100}
    assert body["next"] is None
    assert (body["summary"]["state"], body["summary"]["total_score"]) == ("completed", 100)
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

    assert_error(upload(game, data), status, code)

    assert reads == []
    db.expire_all()
    assert location(game, 1, db).attempts_used == 0
    assert game.last_activity_at == AN_HOUR_AGO  # rolled back: not a game action


# All 409, checked before the photo is read. The 6th attempt fails the location, so only a
# lowered MAX_PHOTO_ATTEMPTS leaves one in Stage 2 with no attempts left.
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

    assert_error(upload(game), 409, code)

    assert reads == []
