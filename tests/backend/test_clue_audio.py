import math
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.content.clue_audio import add_clue_recording
from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.content import AudioAsset, Clue, Location
from app.models.enums import Difficulty, Language

FR_2 = (Difficulty.ADVANCING, Language.FR)
# blocks other uploads to the clue, but not inserts of rows that reference it
LOCK = "FOR NO KEY UPDATE"


@pytest.fixture
def staff(make_user: Callable[..., User]) -> User:
    return make_user(role="staff", can_manage_content=True)


@pytest.fixture
def upload(
    db: Session,
    stored_media_dir: Path,
    staff: User,
    make_location: Callable[..., Location],
    make_audio: Callable[..., bytes],
) -> Callable[..., AudioAsset]:
    """Upload a 3-second MP3 for the French difficulty 2 clue of `location` (a new one if None)."""

    def add(location: Location | None = None, **options: Any) -> AudioAsset:
        location_id = (location or make_location()).id
        return add_clue_recording(location_id, *FR_2, make_audio(3), staff.id, db, **options)

    return add


def primary_flags(location: Location, db: Session) -> dict[UUID, bool]:
    query = select(AudioAsset).join(Clue).where(Clue.location_id == location.id)
    return {asset.id: asset.is_primary for asset in db.scalars(query)}


def test_first_recording_is_stored_as_the_primary_one(
    db: Session, stored_media_dir: Path, staff: User, upload: Callable[..., AudioAsset]
) -> None:
    asset = upload()

    db.expire_all()
    clue = db.get(Clue, asset.clue_id)
    assert clue is not None
    assert (clue.difficulty, clue.language) == FR_2
    assert asset.is_primary
    assert (asset.source_type, asset.provider) == ("uploaded", "teacher")
    assert asset.duration_seconds == pytest.approx(3, abs=0.05)
    stored = (stored_media_dir / asset.file_path).read_bytes()
    assert asset.file_size_kb == math.ceil(len(stored) / 1024)
    assert asset.uploaded_by == staff.id


def test_later_recording_is_not_primary_unless_asked(
    db: Session, make_location: Callable[..., Location], upload: Callable[..., AudioAsset]
) -> None:
    location = make_location()
    first = upload(location)

    second = upload(location)

    db.expire_all()
    assert first.is_primary
    assert not second.is_primary
    assert first.clue_id == second.clue_id


def test_new_primary_replaces_the_old_one_which_keeps_its_row(
    db: Session, make_location: Callable[..., Location], upload: Callable[..., AudioAsset]
) -> None:
    location = make_location()
    old = upload(location)
    extra = upload(location)

    new = upload(location, make_primary=True)

    db.expire_all()
    assert primary_flags(location, db) == {old.id: False, extra.id: False, new.id: True}


def test_stored_file_has_a_random_name_and_no_tags(
    db: Session,
    stored_media_dir: Path,
    make_location: Callable[..., Location],
    make_user: Callable[..., User],
    make_audio: Callable[..., bytes],
) -> None:
    location = make_location(room_number="SCI 333", accepted_answers=["SCI333"])
    data = make_audio(3, title="SCI333")

    asset = add_clue_recording(location.id, *FR_2, data, make_user().id, db)

    key, suffix = asset.file_path.rsplit(".", 1)
    assert (str(UUID(key)), suffix) == (key, "mp3")
    assert b"SCI333" not in (stored_media_dir / asset.file_path).read_bytes()


def test_unknown_location_stores_nothing(
    db: Session,
    stored_media_dir: Path,
    make_user: Callable[..., User],
    make_audio: Callable[..., bytes],
) -> None:
    with pytest.raises(AppError) as error:
        add_clue_recording(uuid4(), *FR_2, make_audio(3), make_user().id, db)

    assert error.value.code == ErrorCode.NOT_FOUND
    assert not list(stored_media_dir.iterdir())


def test_file_is_deleted_when_its_row_cannot_be_saved(
    db: Session,
    stored_media_dir: Path,
    make_location: Callable[..., Location],
    make_audio: Callable[..., bytes],
) -> None:
    unknown_user = uuid4()  # breaks the uploaded_by foreign key on flush

    with pytest.raises(IntegrityError):
        add_clue_recording(make_location().id, *FR_2, make_audio(3), unknown_user, db)

    assert not list(stored_media_dir.iterdir())


def test_rejected_audio_stores_nothing_and_creates_no_clue(
    db: Session,
    stored_media_dir: Path,
    make_location: Callable[..., Location],
    make_user: Callable[..., User],
) -> None:
    location = make_location()

    with pytest.raises(AppError) as error:
        add_clue_recording(location.id, *FR_2, b"not audio", make_user().id, db)

    assert error.value.code == ErrorCode.UNSUPPORTED_MEDIA_TYPE
    assert not list(stored_media_dir.iterdir())
    assert db.scalars(select(Clue).where(Clue.location_id == location.id)).first() is None


@pytest.fixture
def statements(db: Session) -> Iterator[list[str]]:
    """The SQL sent on the test's connection while the test runs."""
    sent: list[str] = []
    connection = db.connection()

    def record(*args: Any) -> None:
        sent.append(args[2])  # (conn, cursor, statement, ...)

    event.listen(connection, "before_cursor_execute", record)
    yield sent
    event.remove(connection, "before_cursor_execute", record)


def test_clue_is_locked_before_the_primary_is_chosen(
    upload: Callable[..., AudioAsset], statements: list[str]
) -> None:
    upload()

    lock = next(i for i, sql in enumerate(statements) if "FROM clues" in sql and LOCK in sql)
    primary = next(i for i, sql in enumerate(statements) if "FROM audio_assets" in sql)
    assert lock < primary


def test_upload_flushes_but_never_commits(
    db: Session,
    make_location: Callable[..., Location],
    upload: Callable[..., AudioAsset],
    transaction_calls: list[str],
) -> None:
    location = make_location()
    upload(location)
    upload(location, make_primary=True)

    assert not (db.new or db.dirty)  # all written, so constraint errors surfaced here
    assert "flush" in transaction_calls
    assert "commit" not in transaction_calls
    assert "rollback" not in transaction_calls
