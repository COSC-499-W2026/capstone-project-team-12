"""Photo uploads that overlap other requests, on real Postgres (A10; spec Testing, "Concurrency
and retries: repeated or concurrent photo uploads never double-count attempts or points ... late
OCR results are discarded"; the A8 and A9 notes: a paused game still takes a result, a shuffled
location never does, and the lock order is user, game, location).

Each request runs in its own thread through the app's own sessions (get_db isn't overridden), so
an upload's two transactions commit as they do in production. The tests set the order instead of
racing on timing: every fake OCR read waits until the test lets it finish (`held_reads`), a
request can be stopped inside its transaction with its locks held (`hold_first_call`), and
`wait_until_blocked_by` waits until another request queues behind that request's lock. What the
requests commit is deleted after each test (`race_game`).

Not `run_together` (conftest), the other concurrency tests' runner: it gives each job a session
it commits once at the end, and orders jobs by delays. The photo route opens its own sessions and
commits twice, and these tests must decide when OCR ends, which a delay can't.
"""

import itertools
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING, Any, NamedTuple
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, Select, delete, func, select, text, update
from sqlalchemy.orm import Session
from verification_fixtures import jpeg

from app.api.v1 import verification as photo_route
from app.config import get_settings
from app.errors import ErrorCode
from app.game import control, shuffle
from app.main import create_app
from app.models.accounts import User
from app.models.engagement import (
    DailyChallenge,
    PointsLedger,
    UserAchievement,
    UserLanguageStats,
    UserStats,
)
from app.models.enums import Difficulty, Language, LocationStatus, PointsSourceType, UserRole
from app.models.game import GameSession, SessionLocation
from app.models.verification import PhotoAttempt
from app.verification.ocr_queue import OcrQueue

if TYPE_CHECKING:  # pytest_plugins in conftest imports it at run time, so pytest rewrites it
    from content_fixtures import SnapshotIds

WAIT = 10  # seconds any step may take before the test fails instead of hanging
PHOTO = "verification/photo"
KEY = "race-photo-1"
SIGN = ("SCI 333", 0.92)  # what every read sees: a clear photo of the room's sign


class RaceGame(NamedTuple):
    student_id: UUID
    game_id: UUID


class Held(NamedTuple):
    backend_pid: int  # the Postgres backend of the held request's transaction
    go_on: threading.Event


@pytest.fixture
def race_game(
    test_engine: Engine,
    committed_location_ids: Callable[..., list[UUID]],
    committed_snapshot_ids: Callable[[UUID], "SnapshotIds"],
) -> Iterator[RaceGame]:
    """A committed ja/basic game at the first of its two locations, which is in Stage 2 with the
    answer SCI333. Deleted after the test with everything its student's requests committed."""
    # all 30 playable locations are committed, so a shuffle or reset can draw new ones
    locations = committed_location_ids(Language.JA, Difficulty.BASIC, count=2)
    snapshots = [committed_snapshot_ids(location_id)._asdict() for location_id in locations]
    now = datetime.now(UTC)
    with Session(test_engine) as session:
        student = User(
            email=f"{uuid4().hex[:12]}@example.com",
            username=f"user_{uuid4().hex[:8]}",
            role=UserRole.STUDENT,
        )
        session.add(student)
        session.flush()
        game = GameSession(
            student_id=student.id,
            target_language=Language.JA,
            difficulty=Difficulty.BASIC,
            last_activity_at=now,
            current_position=1,
        )
        session.add(game)
        session.flush()
        session.add_all(
            SessionLocation(
                session_id=game.id,
                position=position,
                **snapshot,
                accepted_answers_snapshot=["SCI333"],
                gps_points_snapshot=[{"lat": 49.9394, "lng": -119.3948}],
                radius_snapshot=30,
                status=LocationStatus.VERIFYING if position == 1 else LocationStatus.PENDING,
                started_at=now if position == 1 else None,
                arrived_at=now if position == 1 else None,
            )
            for position, snapshot in enumerate(snapshots, start=1)
        )
        ids = RaceGame(student.id, game.id)
        session.commit()
    try:
        yield ids
    finally:
        _delete_student(test_engine, ids)


def _delete_student(engine: Engine, game: RaceGame) -> None:
    """The student and every row the test's requests committed for them, children first."""
    student_id = game.student_id
    games = select(GameSession.id).where(GameSession.student_id == student_id)
    with Session(engine) as session:
        session.execute(text("SET lock_timeout = '10s'"))
        rows = rows_of(game)
        session.execute(delete(PhotoAttempt).where(PhotoAttempt.session_location_id.in_(rows)))
        for model in (PointsLedger, UserAchievement, DailyChallenge, UserLanguageStats, UserStats):
            session.execute(delete(model).where(model.user_id == student_id))
        session.execute(delete(SessionLocation).where(SessionLocation.session_id.in_(games)))
        session.execute(delete(GameSession).where(GameSession.student_id == student_id))
        session.execute(delete(User).where(User.id == student_id))
        session.commit()


@pytest.fixture
def send(
    auth_headers: Callable[[User], dict[str, str]], stored_media_dir: Path
) -> Callable[..., Response]:
    """send(game, method, path, **request): a request to the game's route `path` ("" for the
    game itself) as its student, in a session of its own, as in production. Saved photos go to
    `stored_media_dir`."""
    app = create_app()

    def request(game: RaceGame, method: str, path: str, **kwargs: Any) -> Response:
        headers = auth_headers(User(id=game.student_id)) | kwargs.pop("headers", {})
        url = f"/api/v1/games/{game.game_id}/{path}".removesuffix("/")
        return TestClient(app).request(method, url, headers=headers, **kwargs)

    return request


def photo(key: str | None = None) -> dict[str, Any]:
    """The request arguments of a photo upload, with an Idempotency-Key if one is given."""
    return {
        "files": {"photo": ("sign.jpg", jpeg(), "image/jpeg")},
        "headers": {} if key is None else {"Idempotency-Key": key},
    }


@pytest.fixture
def held_reads(
    ocr_reads: Callable[..., list[int]], monkeypatch: pytest.MonkeyPatch
) -> Queue[threading.Event]:
    """As each OCR read starts, it puts an event here and waits until the test sets it, then
    sees SIGN. The route gets a new OCR queue of the spec's size (not the one-place `ocr_queue`,
    which would turn a second upload away as OCR_BUSY), so no other test's upload holds a place
    in it."""
    started: Queue[threading.Event] = Queue()

    def wait_for_the_test() -> None:
        finish = threading.Event()
        started.put(finish)
        assert finish.wait(WAIT), "the test never let this read finish"

    ocr_reads(SIGN, during=wait_for_the_test)
    ocr_queue = OcrQueue()
    monkeypatch.setattr(photo_route, "get_ocr_queue", lambda: ocr_queue)
    return started


@pytest.fixture
def hold_first_call(monkeypatch: pytest.MonkeyPatch) -> Callable[[object, str], Queue[Held]]:
    """hold_first_call(module, name): the first call of module.name, by any request, waits
    inside that request's transaction (so the locks it took stay held) until the test sets the
    event it reports. The function must take the request's session as an argument. Later calls
    run straight through."""

    def hold(module: object, name: str) -> Queue[Held]:
        real = getattr(module, name)
        held: Queue[Held] = Queue()
        calls = itertools.count()

        def held_call(*args: Any, **kwargs: Any) -> Any:
            if next(calls) == 0:
                db = next(arg for arg in (*args, *kwargs.values()) if isinstance(arg, Session))
                go_on = threading.Event()
                held.put(Held(db.execute(text("SELECT pg_backend_pid()")).scalar_one(), go_on))
                assert go_on.wait(WAIT), "the test never let the held call go on"
            return real(*args, **kwargs)

        monkeypatch.setattr(module, name, held_call)
        return held

    return hold


def wait_until_blocked_by(backend_pid: int, engine: Engine) -> None:
    """Wait until another backend waits for a lock that `backend_pid` holds."""
    blocked = text("SELECT count(*) FROM pg_stat_activity WHERE :pid = ANY(pg_blocking_pids(pid))")
    deadline = time.monotonic() + WAIT
    with engine.connect() as connection:
        while connection.execute(blocked, {"pid": backend_pid}).scalar_one() == 0:
            connection.rollback()  # pg_stat_activity is read once per transaction
            assert time.monotonic() < deadline, f"nothing waited for backend {backend_pid}"
            time.sleep(0.05)


def rows_of(game: RaceGame) -> Select[tuple[UUID]]:
    """The ids of the session locations of every game of the student (a reset adds one)."""
    games = select(GameSession.id).where(GameSession.student_id == game.student_id)
    return select(SessionLocation.id).where(SessionLocation.session_id.in_(games))


def counted(engine: Engine, game: RaceGame) -> tuple[int, list[int], list[str]]:
    """Across the student's games: the attempts used, the location points awarded and the
    photo attempts' statuses, oldest first."""
    rows = rows_of(game)
    with Session(engine) as session:
        used = session.scalar(
            select(func.sum(SessionLocation.attempts_used)).where(SessionLocation.id.in_(rows))
        )
        points = session.scalars(
            select(PointsLedger.points).where(
                PointsLedger.user_id == game.student_id,
                PointsLedger.source_type == PointsSourceType.LOCATION,
            )
        )
        statuses = session.scalars(
            select(PhotoAttempt.status)
            .where(PhotoAttempt.session_location_id.in_(rows))
            .order_by(PhotoAttempt.created_at)
        )
        return used, list(points), list(statuses)


@pytest.mark.parametrize(
    "second_key", [KEY, "another-photo", None], ids=["same key", "another key", "no key"]
)
def test_two_uploads_at_once_read_one_photo_and_count_it_once(
    test_engine: Engine,
    race_game: RaceGame,
    send: Callable[..., Response],
    held_reads: Queue[threading.Event],
    hold_first_call: Callable[[object, str], Queue[Held]],
    assert_error: Callable[[Response, int, ErrorCode], None],
    stored_media_dir: Path,
    second_key: str | None,
) -> None:
    holds = hold_first_call(photo_route, "reserve_attempt")

    with ThreadPoolExecutor() as pool:
        first = pool.submit(send, race_game, "POST", PHOTO, **photo(KEY))
        held = holds.get(timeout=WAIT)  # the first upload has the student, game and location
        second = pool.submit(send, race_game, "POST", PHOTO, **photo(second_key))
        wait_until_blocked_by(held.backend_pid, test_engine)
        held.go_on.set()  # the first commits its attempt; its photo is read next
        read = held_reads.get(timeout=WAIT)
        refused = second.result(timeout=WAIT)  # meanwhile the second was let in, and refused
        read.set()
        response = first.result(timeout=WAIT)

    assert_error(refused, 409, ErrorCode.UPLOAD_IN_PROGRESS)
    assert refused.headers["Retry-After"] == "2"
    assert response.status_code == 200, response.text
    assert (response.json()["result"], response.json()["attempt"]) == ("match", 1)
    assert counted(test_engine, race_game) == (1, [100], ["done"])
    assert len(list(stored_media_dir.iterdir())) == 1  # the refused upload saved no photo


def test_a_photo_read_during_a_pause_counts_and_the_game_moves_on(
    test_engine: Engine,
    race_game: RaceGame,
    send: Callable[..., Response],
    held_reads: Queue[threading.Event],
) -> None:
    with ThreadPoolExecutor() as pool:
        upload = pool.submit(send, race_game, "POST", PHOTO, **photo())
        read = held_reads.get(timeout=WAIT)
        # no lock is held while the photo is read, or the pause would wait for the read
        paused = send(race_game, "POST", "pause")
        read.set()
        response = upload.result(timeout=WAIT)

    assert (paused.status_code, paused.json()["state"]) == (200, "paused")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["location_result"] == {"status": "completed", "points_awarded": 100}
    assert counted(test_engine, race_game) == (1, [100], ["done"])
    assert send(race_game, "GET", "").json()["state"] == "paused"  # the result didn't resume it
    assert send(race_game, "POST", "resume").status_code == 200
    current = send(race_game, "GET", "current-location").json()
    assert (current["position"], current["stage"], current["attempts_used"]) == (
        2,
        "navigation",
        0,
    )


def test_the_late_result_of_a_timed_out_read_is_dropped(
    test_engine: Engine,
    race_game: RaceGame,
    send: Callable[..., Response],
    held_reads: Queue[threading.Event],
    assert_error: Callable[[Response, int, ErrorCode], None],
) -> None:
    # a minute past the timeout: created_at is the database's clock, compared with the app's
    timed_out = timedelta(seconds=get_settings().photo_processing_timeout_seconds, minutes=1)

    with ThreadPoolExecutor() as pool:
        late = pool.submit(send, race_game, "POST", PHOTO, **photo(KEY))
        late_read = held_reads.get(timeout=WAIT)
        with Session(test_engine) as session:  # its attempt has now timed out
            session.execute(
                update(PhotoAttempt)
                .where(PhotoAttempt.session_location_id.in_(rows_of(race_game)))
                .values(created_at=PhotoAttempt.created_at - timed_out)
            )
            session.commit()
        # the same key again: the timed-out attempt is released and the photo read again
        retry = pool.submit(send, race_game, "POST", PHOTO, **photo(KEY))
        retry_read = held_reads.get(timeout=WAIT)
        late_read.set()
        dropped = late.result(timeout=WAIT)
        retry_read.set()
        response = retry.result(timeout=WAIT)

    assert_error(dropped, 503, ErrorCode.OCR_BUSY)
    assert response.status_code == 200, response.text
    assert (response.json()["result"], response.json()["attempt"]) == ("match", 1)
    assert counted(test_engine, race_game) == (1, [100], ["error", "done"])


@pytest.mark.parametrize(
    ("action", "body", "held_step", "status", "code"),
    [
        # held with the game locked: the photo's result waits at the game, holding the student
        (
            "shuffle",
            {"shuffles_used": 0},
            (shuffle, "draw_replacements"),
            200,
            ErrorCode.STATE_CHANGED,
        ),
        # held with the student locked: the photo's result waits at the student, before the game
        ("reset", None, (control, "lock_open_game"), 201, ErrorCode.SESSION_NOT_ACTIVE),
    ],
    ids=["shuffle", "reset"],
)
def test_a_result_that_meets_a_shuffle_or_reset_is_dropped_without_a_deadlock(
    test_engine: Engine,
    race_game: RaceGame,
    send: Callable[..., Response],
    held_reads: Queue[threading.Event],
    hold_first_call: Callable[[object, str], Queue[Held]],
    assert_error: Callable[[Response, int, ErrorCode], None],
    action: str,
    body: dict[str, int] | None,
    held_step: tuple[object, str],
    status: int,
    code: ErrorCode,
) -> None:
    holds = hold_first_call(*held_step)

    with ThreadPoolExecutor() as pool:
        upload = pool.submit(send, race_game, "POST", PHOTO, **photo(KEY))
        read = held_reads.get(timeout=WAIT)
        changed = pool.submit(send, race_game, "POST", action, json=body)
        held = holds.get(timeout=WAIT)
        read.set()  # the photo's result now waits for the shuffle's or reset's lock
        wait_until_blocked_by(held.backend_pid, test_engine)
        held.go_on.set()
        done = changed.result(timeout=WAIT)
        dropped = upload.result(timeout=WAIT)

    assert done.status_code == status, done.text
    assert_error(dropped, 409, code)
    assert counted(test_engine, race_game) == (0, [], ["error"])  # nothing counted anywhere
