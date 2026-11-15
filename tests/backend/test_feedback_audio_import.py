import io
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_clue_audio_import import MB, zip_of

from app.config import get_settings
from app.content.audio_import import import_feedback_audio
from app.content.schemas import AudioImportReport
from app.errors import AppError, ErrorCode
from app.models.content import FeedbackAudio


def phrases(db: Session) -> dict[tuple[str, str], FeedbackAudio]:
    return {(row.code, row.language): row for row in db.scalars(select(FeedbackAudio))}


@pytest.fixture
def import_zip(db: Session, stored_media_dir: Path) -> Callable[[bytes], AudioImportReport]:
    return lambda data: import_feedback_audio(io.BytesIO(data), db)


def test_valid_files_are_imported_and_each_invalid_one_is_reported(
    db: Session,
    stored_media_dir: Path,
    make_audio: Callable[..., bytes],
    import_zip: Callable[[bytes], AudioImportReport],
) -> None:
    names = ["NAV_START_fr.mp3", "VERIFY_SUCCESS_ko.M4A", "NAV_ARRIVED_ja.mp3"]
    names += ["NAV_KEEP_GOING_es.mp3", "NAV_GPS_WEAK_de.mp3"]
    files = [make_audio(3), make_audio(2, "m4a"), bytes(5 * MB + 1), make_audio(61), b"not audio"]

    report = import_zip(zip_of(dict(zip(names, files, strict=True))))

    settings = get_settings()
    assert report.model_dump() == {
        "imported": [{"file": names[0]}, {"file": names[1]}],
        "rejected": [
            {
                "file": names[2],
                "code": ErrorCode.FILE_TOO_LARGE,
                "reason": f"Audio files can be at most {settings.max_media_file_mb} MB",
            },
            {
                "file": names[3],
                "code": ErrorCode.AUDIO_TOO_LONG,
                "reason": f"Recordings can be at most {settings.max_audio_seconds} seconds long",
            },
            {
                "file": names[4],
                "code": ErrorCode.UNSUPPORTED_MEDIA_TYPE,
                "reason": "Upload an MP3 or M4A recording",
            },
        ],
    }
    db.expire_all()
    saved = phrases(db)
    assert set(saved) == {("NAV_START", "fr"), ("VERIFY_SUCCESS", "ko")}
    assert sorted(path.name for path in stored_media_dir.iterdir()) == sorted(
        row.file_path for row in saved.values()
    )


def test_stored_file_has_a_random_name_and_no_tags(
    db: Session,
    stored_media_dir: Path,
    make_audio: Callable[..., bytes],
    import_zip: Callable[[bytes], AudioImportReport],
) -> None:
    import_zip(zip_of({"NAV_START_fr.mp3": make_audio(3, title="NAV_START")}))

    db.expire_all()
    key, suffix = phrases(db)[("NAV_START", "fr")].file_path.rsplit(".", 1)
    assert (str(UUID(key)), suffix) == (key, "mp3")
    assert b"NAV_START" not in (stored_media_dir / f"{key}.{suffix}").read_bytes()


def test_imported_file_replaces_the_phrase_and_clears_its_transcript(
    db: Session,
    make_feedback_audio: Callable[..., FeedbackAudio],
    make_audio: Callable[..., bytes],
    import_zip: Callable[[bytes], AudioImportReport],
) -> None:
    old = make_feedback_audio("NAV_START", "fr", transcript="Allons-y")
    other = make_feedback_audio("NAV_START", "ja", transcript="行こう")
    old_path = old.file_path

    report = import_zip(zip_of({"NAV_START_fr.mp3": make_audio(3)}))

    assert [imported.file for imported in report.imported] == ["NAV_START_fr.mp3"]
    db.expire_all()
    saved = phrases(db)
    assert len(saved) == 2  # replaced, not added
    assert saved[("NAV_START", "fr")].file_path != old_path
    assert saved[("NAV_START", "fr")].transcript is None
    assert (saved[("NAV_START", "ja")].file_path, saved[("NAV_START", "ja")].transcript) == (
        other.file_path,
        "行こう",
    )


@pytest.mark.parametrize(
    "name",
    [
        "NAV_NOPE_fr.mp3",  # not a feedback code
        "nav_start_fr.mp3",
        "NAV_START-fr.mp3",
        "NAV_START_xx.mp3",
        "NAV_START_FR.mp3",
        "NAV_START.mp3",
        "NAV_START_fr.wav",
        "NAV_START_fr.mp3.txt",
        "XNAV_START_fr.mp3",
    ],
)
def test_file_whose_name_is_not_in_the_format_is_not_imported(
    name: str,
    db: Session,
    stored_media_dir: Path,
    make_audio: Callable[..., bytes],
    import_zip: Callable[[bytes], AudioImportReport],
) -> None:
    report = import_zip(zip_of({name: make_audio(3)}))

    assert not report.imported
    [rejected] = report.rejected
    assert (rejected.file, rejected.code) == (name, ErrorCode.VALIDATION_ERROR)
    assert rejected.reason.startswith("The name must be CODE_language.mp3")
    assert not list(stored_media_dir.iterdir())
    assert not phrases(db)


def test_no_file_for_a_phrase_with_several_files_is_imported(
    db: Session,
    make_audio: Callable[..., bytes],
    import_zip: Callable[[bytes], AudioImportReport],
) -> None:
    twice = ["NAV_START_fr.mp3", "copy/NAV_START_fr.m4a"]
    files = {
        twice[0]: make_audio(3),
        twice[1]: make_audio(3, "m4a"),
        "NAV_START_ja.mp3": make_audio(3),
    }

    report = import_zip(zip_of(files))

    assert [imported.file for imported in report.imported] == ["NAV_START_ja.mp3"]
    reason = "The zip has 2 files for NAV_START_fr; keep one of them"
    assert [(rejected.file, rejected.reason) for rejected in report.rejected] == [
        (twice[0], reason),
        (twice[1], reason),
    ]
    assert set(phrases(db)) == {("NAV_START", "ja")}


def test_upload_that_is_not_a_zip_is_refused(
    import_zip: Callable[[bytes], AudioImportReport],
) -> None:
    with pytest.raises(AppError) as error:
        import_zip(b"not a zip")

    assert error.value.code == ErrorCode.UNSUPPORTED_MEDIA_TYPE


def test_file_is_deleted_when_its_row_cannot_be_saved(
    db: Session,
    stored_media_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_audio: Callable[..., bytes],
    import_zip: Callable[[bytes], AudioImportReport],
) -> None:
    def fail() -> None:
        if db.new or db.dirty:  # not the flush before a query
            raise RuntimeError("cannot save")

    monkeypatch.setattr(db, "flush", fail)

    with pytest.raises(RuntimeError):
        import_zip(zip_of({"NAV_START_fr.mp3": make_audio(3)}))

    assert not list(stored_media_dir.iterdir())


def test_import_flushes_but_never_commits(
    db: Session,
    make_audio: Callable[..., bytes],
    import_zip: Callable[[bytes], AudioImportReport],
    transaction_calls: list[str],
) -> None:
    import_zip(zip_of({"NAV_START_fr.mp3": make_audio(3), "bad.mp3": make_audio(3)}))

    assert not (db.new or db.dirty)
    assert "flush" in transaction_calls
    assert "commit" not in transaction_calls
    assert "rollback" not in transaction_calls
