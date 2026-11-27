"""photo_attempts rejects bad data at the database level (spec, Data Models > Game Sessions)."""

from collections.abc import Callable
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import LocationStatus, PhotoAttemptStatus, PhotoResult, SkipReason
from app.models.game import GameSession, SessionLocation
from app.models.verification import PhotoAttempt

MakeAttempt = Callable[..., PhotoAttempt]
DONE = {"status": PhotoAttemptStatus.DONE, "result": PhotoResult.NO_MATCH}
ERROR = {"status": PhotoAttemptStatus.ERROR, "attempt_no": None}


@pytest.fixture
def location(
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> SessionLocation:
    return make_session_location(make_game_session(), 1, status=LocationStatus.VERIFYING)


def test_raw_sql_inserts_get_the_server_defaults(db: Session, location: SessionLocation) -> None:
    attempt = db.execute(
        text(
            "INSERT INTO photo_attempts (session_location_id, attempt_no, content_sha256,"
            " job_id, has_camera_exif) VALUES (:location_id, 1, :sha, :job_id, false)"
            " RETURNING id, status, result, created_at"
        ),
        {"location_id": location.id, "sha": "0" * 64, "job_id": uuid4()},
    ).one()
    assert attempt.id is not None and attempt.created_at is not None
    assert (attempt.status, attempt.result) == (PhotoAttemptStatus.PROCESSING, None)


def test_a_finished_attempt_keeps_its_result(
    db: Session, location: SessionLocation, make_photo_attempt: MakeAttempt
) -> None:
    # phash is a 64-bit hash stored signed, so both ends of bigint must fit
    for attempt_no, phash in [(1, -(2**63)), (2, 2**63 - 1)]:
        attempt = make_photo_attempt(
            location,
            attempt_no=attempt_no,
            status=PhotoAttemptStatus.DONE,
            result=PhotoResult.MATCH,
            ocr_raw_text="ROOM SCI 333",
            extracted_candidate="SCI333",
            ocr_confidence=1.0,
            processing_ms=0,
            response_json={"result": "match", "attempt": attempt_no},
            phash=phash,
            idempotency_key=f"key-{attempt_no}",
        )
        db.expire(attempt)
        assert (attempt.result, attempt.phash) == (PhotoResult.MATCH, phash)
        assert attempt.response_json == {"result": "match", "attempt": attempt_no}


def test_a_done_attempt_may_have_lost_its_response_and_photo(
    location: SessionLocation, make_photo_attempt: MakeAttempt
) -> None:
    """The purge job clears response_json after 24 h and the photo after 90 days."""
    make_photo_attempt(location, **DONE, response_json=None, photo_path=None, phash=None)


def test_an_attempt_number_is_used_once_per_location(
    location: SessionLocation, make_photo_attempt: MakeAttempt
) -> None:
    make_photo_attempt(location, **DONE)
    with pytest.raises(IntegrityError, match="uq_photo_attempts_session_location_id_attempt_no"):
        make_photo_attempt(location, **DONE)


def test_a_location_brought_in_by_a_shuffle_starts_at_attempt_1(
    db: Session,
    location: SessionLocation,
    make_session_location: Callable[..., SessionLocation],
    make_photo_attempt: MakeAttempt,
) -> None:
    make_photo_attempt(location, **DONE)
    location.status, location.skip_reason = LocationStatus.SKIPPED, SkipReason.SHUFFLED
    db.flush()
    game = db.get_one(GameSession, location.session_id)
    replacement = make_session_location(game, 1, status=LocationStatus.VERIFYING)
    make_photo_attempt(replacement, **DONE)


def test_error_attempts_release_their_number(
    location: SessionLocation, make_photo_attempt: MakeAttempt
) -> None:
    make_photo_attempt(location, **ERROR)
    make_photo_attempt(location, **ERROR)
    make_photo_attempt(location, attempt_no=1)


@pytest.mark.parametrize(
    "fields",
    [
        {"status": PhotoAttemptStatus.ERROR, "attempt_no": 1},
        {"status": PhotoAttemptStatus.PROCESSING, "attempt_no": None},
        DONE | {"attempt_no": None},
    ],
)
def test_only_an_error_attempt_has_no_number(
    location: SessionLocation, make_photo_attempt: MakeAttempt, fields: dict[str, object]
) -> None:
    with pytest.raises(IntegrityError, match="ck_photo_attempts_attempt_no_matches_status"):
        make_photo_attempt(location, **fields)


@pytest.mark.parametrize(
    "fields",
    [
        {"status": PhotoAttemptStatus.DONE},
        {"status": PhotoAttemptStatus.PROCESSING, "result": PhotoResult.MATCH},
        ERROR | {"result": PhotoResult.UNCLEAR},
    ],
)
def test_only_a_done_attempt_has_a_result(
    location: SessionLocation, make_photo_attempt: MakeAttempt, fields: dict[str, object]
) -> None:
    with pytest.raises(IntegrityError, match="ck_photo_attempts_result_matches_status"):
        make_photo_attempt(location, **fields)


def test_a_location_reads_one_photo_at_a_time(
    location: SessionLocation, make_photo_attempt: MakeAttempt
) -> None:
    make_photo_attempt(location)
    with pytest.raises(IntegrityError, match="uq_photo_attempts_one_processing"):
        make_photo_attempt(location, attempt_no=2)


def test_finished_attempts_do_not_block_a_new_reading(
    location: SessionLocation, make_photo_attempt: MakeAttempt
) -> None:
    make_photo_attempt(location, **DONE)
    make_photo_attempt(location, **ERROR)
    make_photo_attempt(location, attempt_no=2)


def test_the_processing_index_is_partial_in_the_model_and_the_database(db: Session) -> None:
    # alembic check doesn't compare an index's WHERE, so this catches the two drifting apart
    name = "uq_photo_attempts_one_processing"
    indexdef = db.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"), {"name": name}
    )
    assert indexdef == (
        f"CREATE UNIQUE INDEX {name} ON public.photo_attempts USING btree (session_location_id)"
        " WHERE (status = 'processing'::text)"
    )
    index = next(index for index in PhotoAttempt.__table__.indexes if index.name == name)
    assert index.unique
    assert [column.name for column in index.columns] == ["session_location_id"]
    assert str(index.dialect_options["postgresql"]["where"]) == "status = 'processing'"


def test_an_idempotency_key_is_used_once_per_location(
    location: SessionLocation, make_photo_attempt: MakeAttempt
) -> None:
    make_photo_attempt(location, **DONE, idempotency_key="retry-me")
    match = "uq_photo_attempts_session_location_id_idempotency_key"
    with pytest.raises(IntegrityError, match=match):
        make_photo_attempt(location, attempt_no=2, idempotency_key="retry-me")


def test_keys_are_scoped_to_one_location_and_may_be_missing(
    db: Session,
    location: SessionLocation,
    make_session_location: Callable[..., SessionLocation],
    make_photo_attempt: MakeAttempt,
) -> None:
    make_photo_attempt(location, **DONE, idempotency_key="retry-me")
    make_photo_attempt(location, **DONE, attempt_no=2, idempotency_key=None)
    make_photo_attempt(location, **DONE, attempt_no=3, idempotency_key=None)
    game = db.get_one(GameSession, location.session_id)
    make_photo_attempt(make_session_location(game, 2), idempotency_key="retry-me")


# the quoted name, so "result" doesn't also match "result_matches_status"
@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"status": "queued"}, "status"),
        (DONE | {"result": "maybe"}, "result"),
        ({"attempt_no": 0}, "attempt_no"),
        ({"ocr_confidence": -0.01}, "ocr_confidence"),
        ({"ocr_confidence": 1.01}, "ocr_confidence"),
        ({"processing_ms": -1}, "processing_ms"),
        ({"content_sha256": "A" * 64}, "content_sha256"),
        ({"content_sha256": "a" * 63}, "content_sha256"),
        ({"content_sha256": "a" * 65}, "content_sha256"),
        ({"content_sha256": "g" * 64}, "content_sha256"),
    ],
)
def test_attempt_values_stay_in_range(
    location: SessionLocation,
    make_photo_attempt: MakeAttempt,
    fields: dict[str, object],
    constraint: str,
) -> None:
    with pytest.raises(IntegrityError, match=f'"ck_photo_attempts_{constraint}"'):
        make_photo_attempt(location, **fields)


def test_an_attempt_belongs_to_a_real_location(make_photo_attempt: MakeAttempt) -> None:
    missing = SessionLocation(id=uuid4())  # never added to the session
    match = '"fk_photo_attempts_session_location_id_session_locations"'
    with pytest.raises(IntegrityError, match=match):
        make_photo_attempt(missing)
