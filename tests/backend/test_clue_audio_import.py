import io
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content import audio_import
from app.content.audio_import import import_clue_audio
from app.content.schemas import AudioImportReport
from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.content import AudioAsset, Clue, Location

MB = 1024 * 1024


def zip_of(files: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    """A zip of `files` in this order, named exactly as given ('\\' included)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            info.filename = name  # on Windows, ZipInfo turns '\\' into '/'
            archive.writestr(info, data, compression)
    return buffer.getvalue()


def answer(location: Location) -> str:
    """The room number as file names give it, e.g. TST1000."""
    assert location.room_number is not None
    return location.room_number.replace(" ", "")


def file_name(
    location: Location, difficulty: int = 2, language: str = "fr", ext: str = "mp3"
) -> str:
    return f"{answer(location)}_{difficulty}_{language}.{ext}"


def recordings(location: Location, db: Session) -> list[tuple[int, str, bool]]:
    """(difficulty, language, is_primary) of each recording of the location."""
    query = (
        select(Clue.difficulty, Clue.language, AudioAsset.is_primary)
        .join(AudioAsset)
        .where(Clue.location_id == location.id)
        .order_by(Clue.difficulty, Clue.language, AudioAsset.is_primary)
    )
    return [(row.difficulty, row.language, row.is_primary) for row in db.execute(query)]


@pytest.fixture
def staff(make_user: Callable[..., User]) -> User:
    return make_user(role="staff", can_manage_content=True)


@pytest.fixture
def import_zip(
    db: Session, stored_media_dir: Path, staff: User
) -> Callable[[bytes], AudioImportReport]:
    return lambda data: import_clue_audio(io.BytesIO(data), staff.id, db)


def test_valid_files_are_imported_and_each_invalid_one_is_reported(
    db: Session,
    stored_media_dir: Path,
    staff: User,
    make_location: Callable[..., Location],
    make_audio: Callable[..., bytes],
    import_zip: Callable[[bytes], AudioImportReport],
) -> None:
    location, other = make_location(), make_location()
    names = [
        file_name(location),
        file_name(other, 1, "ja"),
        file_name(other, 2, "ja"),
        file_name(other, 3, "ja"),
        file_name(other, 1, "ko", "M4A"),  # the extension's case does not matter
    ]
    files = [make_audio(3), bytes(5 * MB + 1), make_audio(61), b"not audio", make_audio(2, "m4a")]

    report = import_zip(zip_of(dict(zip(names, files, strict=True))))

    settings = get_settings()
    assert report.model_dump() == {
        "imported": [{"file": names[0]}, {"file": names[4]}],
        "rejected": [
            {
                "file": names[1],
                "code": ErrorCode.FILE_TOO_LARGE,
                "reason": f"Audio files can be at most {settings.max_media_file_mb} MB",
            },
            {
                "file": names[2],
                "code": ErrorCode.AUDIO_TOO_LONG,
                "reason": f"Recordings can be at most {settings.max_audio_seconds} seconds long",
            },
            {
                "file": names[3],
                "code": ErrorCode.UNSUPPORTED_MEDIA_TYPE,
                "reason": "Upload an MP3 or M4A recording",
            },
        ],
    }
    db.expire_all()
    assert recordings(location, db) == [(2, "fr", True)]
    assert recordings(other, db) == [(1, "ko", True)]
    assets = db.scalars(select(AudioAsset).where(AudioAsset.uploaded_by == staff.id)).all()
    assert sorted(path.name for path in stored_media_dir.iterdir()) == sorted(
        asset.file_path for asset in assets
    )


def test_imported_recording_becomes_primary_and_the_old_one_keeps_its_row(
    db: Session,
    make_location: Callable[..., Location],
    make_clue: Callable[..., Clue],
    make_audio_asset: Callable[..., AudioAsset],
    make_audio: Callable[..., bytes],
    import_zip: Callable[[bytes], AudioImportReport],
) -> None:
    location = make_location()
    old = make_audio_asset(make_clue(location, difficulty=2, language="fr"), is_primary=True)

    import_zip(zip_of({file_name(location): make_audio(3)}))

    db.expire_all()
    assert recordings(location, db) == [(2, "fr", False), (2, "fr", True)]
    assert not old.is_primary


@pytest.mark.parametrize(
    "name",
    [
        "{room}-2-fr.mp3",
        "{lower}_2_fr.mp3",
        "{spaced}_2_fr.mp3",
        "{room}0_2_fr.mp3",  # 5 digits
        "{room}_4_fr.mp3",
        "{room}_2_xx.mp3",
        "{room}_2_FR.mp3",
        "{room}_2_fr.wav",
        "{room}_2_fr.mp3.txt",
        "{room}_2.mp3",
    ],
)
def test_file_whose_name_is_not_in_the_format_is_not_imported(
    name: str,
    db: Session,
    stored_media_dir: Path,
    make_location: Callable[..., Location],
    make_audio: Callable[..., bytes],
    import_zip: Callable[[bytes], AudioImportReport],
) -> None:
    location = make_location()
    room = answer(location)
    name = name.format(room=room, lower=room.lower(), spaced=location.room_number)

    report = import_zip(zip_of({name: make_audio(3)}))

    assert not report.imported
    [rejected] = report.rejected
    assert (rejected.file, rejected.code) == (name, ErrorCode.VALIDATION_ERROR)
    assert rejected.reason.startswith("The name must be ROOM_difficulty_language.mp3")
    assert not list(stored_media_dir.iterdir())
    assert not recordings(location, db)


def test_room_number_no_location_has_is_rejected(
    stored_media_dir: Path,
    make_audio: Callable[..., bytes],
    import_zip: Callable[[bytes], AudioImportReport],
) -> None:
    report = import_zip(zip_of({"ZZZ9999_1_ja.mp3": make_audio(3)}))

    assert report.model_dump()["rejected"] == [
        {
            "file": "ZZZ9999_1_ja.mp3",
            "code": ErrorCode.VALIDATION_ERROR,
            "reason": "No location has room number ZZZ 9999",
        }
    ]
    assert not list(stored_media_dir.iterdir())


def test_no_file_for_a_clue_with_several_files_is_imported(
    db: Session,
    make_location: Callable[..., Location],
    make_audio: Callable[..., bytes],
    import_zip: Callable[[bytes], AudioImportReport],
) -> None:
    location = make_location()
    twice = [file_name(location), f"copy/{file_name(location, ext='m4a')}"]
    files = {twice[0]: make_audio(3), twice[1]: make_audio(3, "m4a")}

    report = import_zip(zip_of(files | {file_name(location, 1, "ja"): make_audio(3)}))

    assert [imported.file for imported in report.imported] == [file_name(location, 1, "ja")]
    reason = f"The zip has 2 files for {answer(location)}_2_fr; keep one of them"
    assert [(rejected.file, rejected.reason) for rejected in report.rejected] == [
        (twice[0], reason),
        (twice[1], reason),
    ]
    assert recordings(location, db) == [(1, "ja", True)]


def test_folders_and_hidden_files_are_skipped(
    make_location: Callable[..., Location],
    make_audio: Callable[..., bytes],
    import_zip: Callable[[bytes], AudioImportReport],
) -> None:
    location = make_location()
    first, second = file_name(location), file_name(location, 1, "ja")
    files = {
        "clues/": b"",
        "clues\\fr\\": b"",  # folders as some Windows tools write them
        f"clues\\fr\\{first}": make_audio(3),
        f".recordings/{second}": make_audio(3),  # only the file's own name counts
        f"__MACOSX/clues/fr/._{first}": b"resource fork",
        "clues/.DS_Store": b"folder settings",
    }

    report = import_zip(zip_of(files))

    imported = [{"file": f"clues/fr/{first}"}, {"file": f".recordings/{second}"}]
    assert report.model_dump() == {"imported": imported, "rejected": []}


@pytest.mark.parametrize("data", [b"", b"not a zip", b"PK cut short"])
def test_upload_that_is_not_a_zip_is_refused(
    data: bytes, import_zip: Callable[[bytes], AudioImportReport]
) -> None:
    with pytest.raises(AppError) as error:
        import_zip(data)

    assert error.value.code == ErrorCode.UNSUPPORTED_MEDIA_TYPE
    assert error.value.message == "Upload a zip file"


def test_damaged_file_is_rejected_and_the_others_imported(
    db: Session,
    make_location: Callable[..., Location],
    make_audio: Callable[..., bytes],
    import_zip: Callable[[bytes], AudioImportReport],
) -> None:
    location = make_location()
    damaged, intact = file_name(location, 1, "ja"), file_name(location)
    data = bytearray(zip_of({damaged: make_audio(3), intact: make_audio(3)}, zipfile.ZIP_STORED))
    data[30 + len(damaged)] ^= 0xFF  # its first byte, after a 30-byte header and the name

    report = import_zip(bytes(data))

    assert [imported.file for imported in report.imported] == [intact]
    assert report.model_dump()["rejected"] == [
        {
            "file": damaged,
            "code": ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            "reason": "The file cannot be read from the zip: it is damaged",
        }
    ]
    assert recordings(location, db) == [(2, "fr", True)]


def test_error_after_the_file_is_read_is_not_reported_as_unreadable(
    monkeypatch: pytest.MonkeyPatch,
    make_location: Callable[..., Location],
    make_audio: Callable[..., bytes],
    import_zip: Callable[[bytes], AudioImportReport],
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise zipfile.BadZipFile("not from reading the zip")

    monkeypatch.setattr(audio_import, "add_clue_recording", fail)

    with pytest.raises(zipfile.BadZipFile):  # not a rejected file: what it wrote would be kept
        import_zip(zip_of({file_name(make_location()): make_audio(3)}))


def test_import_flushes_but_never_commits(
    db: Session,
    make_location: Callable[..., Location],
    make_audio: Callable[..., bytes],
    import_zip: Callable[[bytes], AudioImportReport],
    transaction_calls: list[str],
) -> None:
    location = make_location()
    files = {file_name(location): make_audio(3), "bad.mp3": make_audio(3)}

    import_zip(zip_of(files))

    assert not (db.new or db.dirty)
    assert "flush" in transaction_calls
    assert "commit" not in transaction_calls
    assert "rollback" not in transaction_calls
