"""5.4 Bulk import: clue and feedback recordings from a zip (A20).

Each file is named {ROOM}_{difficulty}_{language}.mp3, e.g. SCI333_2_fr.mp3: the room number as
students type it (capital letters, no space), the difficulty and the language code; an M4A file
ends in .m4a (the extension in any case). Only the file's own name counts, not the folders it is
in ('/' or '\\', which some Windows tools write). Folders and hidden files (such as the
.DS_Store and __MACOSX/._ files macOS adds to a zip) are skipped and left out of the report.

Each file is read whole (at most `max_media_file_mb`) before anything is imported, then checked
as a single upload is (add_clue_recording) and becomes its clue's primary recording; the old
primary keeps its row, as an open game may still play it (so importing the same zip again adds a
new primary recording to each clue, as uploading a file twice does). The valid files are
imported even when others are rejected, and the report gives each rejected file's reason: a name
not in the format, more than one file for a clue (none of them is imported, as which one is
meant cannot be told), a file that cannot be read from the zip, a room number no location has,
or the audio checks' reasons. Nothing is written for a rejected file. An upload that is not a
zip, or that has more than `max_import_files` files, is refused whole. Files are reported by
their path in the zip, as given.

A feedback zip works the same way with files named {CODE}_{language}.mp3 (or .m4a), e.g.
NAV_START_fr.mp3, CODE being one of the feedback codes. Each file replaces the language's phrase
for that code (its transcript is cleared, as it no longer matches) or adds it; the replaced file
stays in storage, as the spec has no cleanup job.

Flushes and never commits: the router owns the transaction.
"""

import io
import lzma
import re
import zipfile
import zlib
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import PurePosixPath
from typing import IO
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content.audio_files import check_audio, read_audio_file
from app.content.clue_audio import add_clue_recording
from app.content.locations import invalid_field
from app.content.schemas import AudioImportReport, ImportedFile, RejectedFile, room_number_sign
from app.errors import AppError, ErrorCode
from app.models.content import FeedbackAudio, Location
from app.models.enums import Difficulty, FeedbackCode, Language
from app.services.storage import get_storage_service

_DIFFICULTIES = [str(difficulty.value) for difficulty in Difficulty]
# the groups name the clue, so SCI333_2_fr.mp3 and SCI333_2_fr.m4a are two files for one clue
_CLUE_FILE_NAME = re.compile(
    rf"([A-Z]{{3}}[0-9]{{3,4}})_({'|'.join(_DIFFICULTIES)})_({'|'.join(Language)})"
    r"\.(?i:mp3|m4a)"
)
_CLUE_FILE_FORMAT = (
    "ROOM_difficulty_language.mp3 (or .m4a), e.g. SCI333_2_fr.mp3: the room number in capitals"
    f" without a space, difficulty {', '.join(_DIFFICULTIES)}, language {', '.join(Language)}"
)
_FEEDBACK_FILE_NAME = re.compile(
    rf"({'|'.join(FeedbackCode)})_({'|'.join(Language)})\.(?i:mp3|m4a)"
)
_FEEDBACK_FILE_FORMAT = (
    "CODE_language.mp3 (or .m4a), e.g. NAV_START_fr.mp3: the code in capitals,"
    f" one of {', '.join(FeedbackCode)}; language {', '.join(Language)}"
)
_ENCRYPTED = 0x1  # bit 0 of a zip entry's flags
# what zipfile raises for a damaged or unusual zip, when it is opened or a file is read: bad
# headers or CRC (BadZipFile), bad compressed data (zlib.error, LZMAError, and OSError from
# bzip2), an offset before the start of the file (ValueError, or OSError on a file on disk), a
# name flagged UTF-8 that is not (UnicodeDecodeError, a ValueError) and features zipfile does
# not support (NotImplementedError)
_ZIP_ERRORS = (
    zipfile.BadZipFile,
    zlib.error,
    lzma.LZMAError,
    OSError,
    ValueError,
    NotImplementedError,
)

# imports one file: the groups of its name, and the file's bytes
_ImportFile = Callable[[tuple[str, ...], bytes], None]
_Outcome = ImportedFile | RejectedFile


def import_clue_audio(zip_file: IO[bytes], uploaded_by: UUID, db: Session) -> AudioImportReport:
    """Import each valid recording in `zip_file` as its clue's primary recording and report
    every file, imported or rejected with its reason; UNSUPPORTED_MEDIA_TYPE if the upload is
    not a zip, VALIDATION_ERROR on `file` if it has more than `max_import_files` files."""

    def import_file(clue: tuple[str, ...], data: bytes) -> None:
        room, difficulty, language = clue
        location_id = _location_id(room, db)
        add_clue_recording(
            location_id,
            Difficulty(int(difficulty)),
            Language(language),
            io.BytesIO(data),
            uploaded_by,
            db,
            make_primary=True,
        )

    return _import_zip(zip_file, _CLUE_FILE_NAME, _CLUE_FILE_FORMAT, import_file, db)


def import_feedback_audio(zip_file: IO[bytes], db: Session) -> AudioImportReport:
    """Import each valid recording in `zip_file` as the feedback phrase it is named for and
    report every file, imported or rejected with its reason; UNSUPPORTED_MEDIA_TYPE if the upload
    is not a zip, VALIDATION_ERROR on `file` if it has more than `max_import_files` files."""

    def import_file(phrase: tuple[str, ...], data: bytes) -> None:
        code, language = phrase
        _replace_phrase(code, language, data, db)

    return _import_zip(zip_file, _FEEDBACK_FILE_NAME, _FEEDBACK_FILE_FORMAT, import_file, db)


def _replace_phrase(code: str, language: str, data: bytes, db: Session) -> None:
    """Check the recording and make it the phrase `code` in `language`, adding the phrase if the
    language has none; the checks' AppError, nothing stored then. One upsert, so two imports
    adding the same phrase at once do not fail: the later one waits, then replaces it."""
    checked = check_audio(io.BytesIO(data))
    storage = get_storage_service()
    key = storage.save(checked.data, checked.suffix)
    phrase = insert(FeedbackAudio).values(code=code, language=language, file_path=key)
    replace = {"file_path": key, "transcript": None}
    try:
        db.execute(phrase.on_conflict_do_update(index_elements=["code", "language"], set_=replace))
    except Exception:
        storage.delete(key)  # no row points at it
        raise


def _location_id(room: str, db: Session) -> UUID:
    """The location with the room number of a file name ("SCI333"); VALIDATION_ERROR if none.
    A deactivated location counts, as for a single upload: its audio can be ready before it is
    reactivated."""
    room_number = room_number_sign(room)
    location = db.scalars(select(Location).filter_by(room_number=room_number)).one_or_none()
    if location is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, f"No location has room number {room_number}")
    return location.id


def _import_zip(
    zip_file: IO[bytes],
    name: re.Pattern[str],
    name_format: str,
    import_file: _ImportFile,
    db: Session,
) -> AudioImportReport:
    """Report every file of the zip that is not skipped: rejected if its name does not match
    `name` (described by `name_format`), if another file's name gives the same groups, if it
    cannot be read or if `import_file` raises AppError; imported otherwise.

    Files are imported in the order of their name's groups, not the zip's, so two imports at
    once lock the clues or phrases they share in the same order and cannot deadlock; each list
    of the report keeps the zip's order. Each file is imported in a savepoint, so a rejected one
    leaves nothing in the database.
    """
    with _open_zip(zip_file) as archive:
        files = [
            (info, name.fullmatch(PurePosixPath(_path(info)).name))
            for info in archive.infolist()
            if not _skipped(info)
        ]
        _check_file_count(len(files))
        counts = Counter(match.groups() for _, match in files if match)

        def import_one(info: zipfile.ZipInfo, match: re.Match[str] | None) -> _Outcome:
            try:
                key = _file_key(match, counts, name_format)
                data = _read_file(archive, info)
                with db.begin_nested():
                    import_file(key, data)
            except AppError as error:
                return RejectedFile(file=_path(info), code=error.code, reason=error.message)
            return ImportedFile(file=_path(info))

        by_name = sorted(range(len(files)), key=lambda index: _lock_order(files[index][1]))
        outcomes = {index: import_one(*files[index]) for index in by_name}
    return _report(outcomes[index] for index in range(len(files)))


def _lock_order(match: re.Match[str] | None) -> tuple[str, ...]:
    """Where a file comes in the import: by its name's groups, a file with a wrong name first
    (it locks nothing)."""
    return match.groups() if match else ()


def _report(outcomes: Iterable[_Outcome]) -> AudioImportReport:
    report = AudioImportReport()
    for outcome in outcomes:
        if isinstance(outcome, ImportedFile):
            report.imported.append(outcome)
        else:
            report.rejected.append(outcome)
    return report


def _open_zip(zip_file: IO[bytes]) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(zip_file)
    except _ZIP_ERRORS as error:
        raise AppError(ErrorCode.UNSUPPORTED_MEDIA_TYPE, "Upload a zip file") from error


def _path(info: zipfile.ZipInfo) -> str:
    """The file's path in the zip with '/' between folders. The name as stored, as zipfile
    turns '\\' into '/' only on Windows."""
    return info.orig_filename.replace("\\", "/")


def _skipped(info: zipfile.ZipInfo) -> bool:
    """A folder, or a hidden file such as .DS_Store or the ._ file macOS adds under __MACOSX/
    for each file. Files in a hidden folder are not skipped: only the file's own name counts."""
    path = _path(info)
    return path.endswith("/") or PurePosixPath(path).name.startswith(".")


def _check_file_count(count: int) -> None:
    limit = get_settings().max_import_files
    if count > limit:
        message = f"The zip has {count} files; one import takes at most {limit}"
        raise invalid_field("file", message)


def _file_key(
    match: re.Match[str] | None, counts: Counter[tuple[str, ...]], name_format: str
) -> tuple[str, ...]:
    """The groups of a file's name; VALIDATION_ERROR if it did not match or another file's name
    gives the same groups."""
    if match is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, f"The name must be {name_format}")
    key = match.groups()
    if counts[key] > 1:
        message = f"The zip has {counts[key]} files for {'_'.join(key)}; keep one of them"
        raise AppError(ErrorCode.VALIDATION_ERROR, message)
    return key


def _read_file(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """The file's bytes, or FILE_TOO_LARGE over `max_media_file_mb` (one byte more is read at
    most, whatever size the zip states); UNSUPPORTED_MEDIA_TYPE if it is encrypted, damaged or
    compressed in a way zipfile cannot read."""
    message = "The file cannot be read from the zip: it is encrypted, damaged or compressed in"
    unreadable = AppError(ErrorCode.UNSUPPORTED_MEDIA_TYPE, f"{message} an unsupported way")
    if info.flag_bits & _ENCRYPTED:
        raise unreadable
    try:
        with archive.open(info) as file:
            return read_audio_file(file)
    except _ZIP_ERRORS as error:
        raise unreadable from error
