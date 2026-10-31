"""Two audio zip imports at once, on the real database (A20, PR 8b review).

The other tests run in one transaction that is rolled back, which a second connection cannot see,
so these commit their rows through their own sessions and delete exactly what they committed in
teardown. Each import runs in its own thread and session and commits, as two requests would.
"""

import contextlib
import io
import random
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session
from test_clue_audio_import import zip_of

from app.content import audio_import
from app.models.accounts import User
from app.models.content import AudioAsset, Clue, FeedbackAudio, Location
from app.models.enums import FeedbackCode, Language, UserRole

# an import holds its first lock this long, waiting for the other to take its own first one
HOLD_SECONDS = 2
JOIN_SECONDS = 15  # Postgres reports a deadlock after deadlock_timeout (1 s by default)

_Import = Callable[[Session], Any]


class Clues(NamedTuple):
    staff_id: UUID
    room: str  # as file names give it, e.g. QZX1234


def _cleanup_session(test_engine: Engine) -> Session:
    cleanup = Session(test_engine)
    cleanup.execute(text("SET lock_timeout = '10s'"))  # a stuck import cannot hang pytest
    return cleanup


@pytest.fixture
def clues(test_engine: Engine, stored_media_dir: Path) -> Iterator[Clues]:
    """A committed content manager, and a location with committed clues for difficulty 1 and 2
    in Japanese, under a room number no other location has."""
    with Session(test_engine) as setup:
        taken = set(setup.scalars(select(Location.room_number)))
        room_number = next(
            room
            for room in (f"QZX {random.randint(1000, 9999)}" for _ in range(100))
            if room not in taken
        )
        staff = User(
            email=f"{uuid4().hex[:12]}@example.com",
            username=f"user_{uuid4().hex[:8]}",
            role=UserRole.STAFF,
            can_manage_content=True,
        )
        location = Location(
            name="Concurrent imports",
            building_name="Test",
            room_number=room_number,
            accepted_answers=[room_number.replace(" ", "")],
            gps_points=[{"lat": 49.9394, "lng": -119.3948}],
            gps_radius_meters=30,
        )
        setup.add_all([staff, location])
        setup.flush()
        setup.add_all(
            Clue(location_id=location.id, difficulty=difficulty, language=Language.JA)
            for difficulty in (1, 2)
        )
        setup.commit()
        staff_id, location_id = staff.id, location.id
    try:
        yield Clues(staff_id, room_number.replace(" ", ""))
    finally:
        with _cleanup_session(test_engine) as cleanup:
            cleanup.execute(delete(AudioAsset).where(AudioAsset.uploaded_by == staff_id))
            cleanup.execute(delete(Clue).where(Clue.location_id == location_id))
            cleanup.execute(delete(Location).where(Location.id == location_id))
            cleanup.execute(delete(User).where(User.id == staff_id))
            cleanup.commit()


@pytest.fixture
def absent_phrases(test_engine: Engine, stored_media_dir: Path) -> Iterator[list[str]]:
    """The file names of two feedback phrases of one language the database does not have yet.
    Phrases are real content on a development database: the test only adds these two, and
    teardown deletes them; it is skipped when every language has all its phrases."""
    with Session(test_engine) as lookup:
        existing = set(lookup.execute(select(FeedbackAudio.code, FeedbackAudio.language)))
    free = {
        language: [code for code in FeedbackCode if (code, language) not in existing]
        for language in Language
    }
    language = next((language for language, codes in free.items() if len(codes) >= 2), None)
    if language is None:
        pytest.skip("every language has its phrases: no phrase this test may add and delete")
    codes = free[language][:2]
    try:
        yield [f"{code}_{language}.mp3" for code in codes]
    finally:
        with _cleanup_session(test_engine) as cleanup:
            for code in codes:
                cleanup.execute(delete(FeedbackAudio).filter_by(code=code, language=language))
            cleanup.commit()


def hold_first_lock(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Make each thread wait, after its first file of audio_import.`name`, until the other
    thread has done its first file too, or HOLD_SECONDS have passed. When both get there, each
    holds a lock the other may need next: imports in opposite orders would then deadlock. When
    the order is right, the second waits for the first's lock and never gets there."""
    real = getattr(audio_import, name)
    both_hold = threading.Barrier(2, timeout=HOLD_SECONDS)
    waited = threading.local()

    def import_then_wait(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        if not getattr(waited, "done", False):
            waited.done = True
            # broken when the other one is waiting for a lock this one holds
            with contextlib.suppress(threading.BrokenBarrierError):
                both_hold.wait()
        return result

    monkeypatch.setattr(audio_import, name, import_then_wait)


def run_at_once(test_engine: Engine, *imports: _Import) -> list[BaseException]:
    """Run each import in its own thread and session and commit it; the errors raised."""
    errors: list[BaseException] = []  # a thread's exception is otherwise only printed

    def run(do_import: _Import) -> None:
        try:
            with Session(test_engine) as session:
                session.execute(text("SET lock_timeout = '10s'"))
                do_import(session)
                session.commit()
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=run, args=(do_import,)) for do_import in imports]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=JOIN_SECONDS)
    assert not any(thread.is_alive() for thread in threads), "an import is stuck"
    return errors


def test_clue_imports_in_opposite_zip_orders_both_commit(
    test_engine: Engine,
    clues: Clues,
    make_audio: Callable[..., bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = f"{clues.room}_1_ja.mp3", f"{clues.room}_2_ja.mp3"
    clip = make_audio(1)
    zips = [zip_of({first: clip, second: clip}), zip_of({second: clip, first: clip})]
    hold_first_lock(monkeypatch, "add_clue_recording")

    errors = run_at_once(
        test_engine,
        *(
            lambda db, data=data: audio_import.import_clue_audio(
                io.BytesIO(data), clues.staff_id, db
            )
            for data in zips
        ),
    )

    assert errors == []
    with Session(test_engine) as check:
        uploaded = select(AudioAsset).where(AudioAsset.uploaded_by == clues.staff_id)
        assert len(check.scalars(uploaded).all()) == 4  # each import added both recordings


def test_feedback_imports_in_opposite_zip_orders_both_commit(
    test_engine: Engine,
    absent_phrases: list[str],
    make_audio: Callable[..., bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = absent_phrases
    clip = make_audio(1)
    with Session(test_engine) as setup:  # the phrases exist, so each import locks their rows
        setup.execute(text("SET lock_timeout = '10s'"))
        audio_import.import_feedback_audio(io.BytesIO(zip_of({first: clip, second: clip})), setup)
        setup.commit()
    zips = [zip_of({first: clip, second: clip}), zip_of({second: clip, first: clip})]
    hold_first_lock(monkeypatch, "_replace_phrase")

    errors = run_at_once(
        test_engine,
        *(
            lambda db, data=data: audio_import.import_feedback_audio(io.BytesIO(data), db)
            for data in zips
        ),
    )

    assert errors == []


def test_feedback_imports_adding_the_same_phrase_at_once_both_commit(
    test_engine: Engine,
    absent_phrases: list[str],
    make_audio: Callable[..., bytes],
    stored_media_dir: Path,
) -> None:
    """The first import adds the phrase and keeps its transaction open until the second is
    seen waiting for that row; the second must then replace the phrase, not fail."""
    name = absent_phrases[0]
    code, language = name.removesuffix(".mp3").rsplit("_", 1)
    data = zip_of({name: make_audio(1)})
    second_pid: list[int] = []

    with Session(test_engine) as first:
        first.execute(text("SET lock_timeout = '10s'"))
        audio_import.import_feedback_audio(io.BytesIO(data), first)
        first_key = first.get_one(FeedbackAudio, (code, language)).file_path

        def second(db: Session) -> None:
            second_pid.append(db.scalars(text("SELECT pg_backend_pid()")).one())
            audio_import.import_feedback_audio(io.BytesIO(data), db)

        errors: list[BaseException] = []
        thread = threading.Thread(target=lambda: errors.extend(run_at_once(test_engine, second)))
        thread.start()
        with Session(test_engine) as watcher:
            waiting = text(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE pid = :pid AND wait_event_type = 'Lock'"
            )
            for _ in range(100):  # up to 10 s
                if second_pid and watcher.scalars(waiting, {"pid": second_pid[0]}).one():
                    break
                watcher.rollback()  # pg_stat_activity is a snapshot per transaction
                time.sleep(0.1)
            else:
                pytest.fail("the second import never waited for the first one's row")
        first.commit()
        thread.join(timeout=JOIN_SECONDS)

    assert errors == []
    with Session(test_engine) as check:
        phrase = check.get_one(FeedbackAudio, (code, language))
        stored = {path.name for path in stored_media_dir.iterdir()}
        assert phrase.file_path != first_key and phrase.file_path in stored  # the second won
