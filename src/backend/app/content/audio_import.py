"""5.4 Bulk import: clue recordings from a zip (A20).

Each file is named {ROOM}_{difficulty}_{language}.mp3, e.g. SCI333_2_fr.mp3: the room number as
students type it (capital letters, no space), the difficulty and the language code; an M4A file
ends in .m4a (the extension in any case). Only the file's own name counts, not the folders it is
in ('/' or '\\', which some Windows tools write). Folders and hidden files (such as the
.DS_Store and __MACOSX/._ files macOS adds to a zip) are skipped and left out of the report.

Each file is read whole (at most `max_media_file_mb`) before anything is imported, then checked
as a single upload is (add_clue_recording) and becomes its clue's primary recording; the old
primary keeps its row, as an open game may still play it. The valid files are imported even when
others are rejected, and the report gives each rejected file's reason: a name not in the format,
more than one file for a clue (none of them is imported, as which one is meant cannot be told),
a file that cannot be read from the zip, a room number no location has, or the audio checks'
reasons. Nothing is written for a rejected file. An upload that is not a zip is refused whole.

Flushes and never commits: the router owns the transaction.
"""

import io
import re
import zipfile
from collections import Counter
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import IO
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content.audio_files import read_audio_file
from app.content.clue_audio import add_clue_recording
from app.content.schemas import AudioImportReport, ImportedFile, RejectedFile, room_number_sign
from app.errors import AppError, ErrorCode
from app.models.content import Location
from app.models.enums import Difficulty, Language

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
# imports one file: the groups of its name, and the file's bytes
_ImportFile = Callable[[tuple[str, ...], bytes], None]


def import_clue_audio(zip_file: IO[bytes], uploaded_by: UUID, db: Session) -> AudioImportReport:
    """Import each valid recording in `zip_file` as its clue's primary recording and report
    every file, imported or rejected with its reason; UNSUPPORTED_MEDIA_TYPE if the upload is
    not a zip."""

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

    return _import_zip(zip_file, _CLUE_FILE_NAME, _CLUE_FILE_FORMAT, import_file)


def _location_id(room: str, db: Session) -> UUID:
    """The location with the room number of a file name ("SCI333"); VALIDATION_ERROR if none."""
    room_number = room_number_sign(room)
    location = db.scalars(select(Location).filter_by(room_number=room_number)).one_or_none()
    if location is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, f"No location has room number {room_number}")
    return location.id


def _import_zip(
    zip_file: IO[bytes], name: re.Pattern[str], name_format: str, import_file: _ImportFile
) -> AudioImportReport:
    """Report every file of the zip that is not skipped, in its order: rejected if its name
    does not match `name` (described by `name_format`), if another file's name gives the same
    groups, if it cannot be read or if `import_file` raises AppError; imported otherwise."""
    report = AudioImportReport()
    with _open_zip(zip_file) as archive:
        files = [
            (info, name.fullmatch(PurePosixPath(_path(info)).name))
            for info in archive.infolist()
            if not _skipped(info)
        ]
        counts = Counter(match.groups() for _, match in files if match)
        for info, match in files:
            try:
                key = _file_key(match, counts, name_format)
                import_file(key, _read_file(archive, info))
            except AppError as error:
                rejected = RejectedFile(file=_path(info), code=error.code, reason=error.message)
                report.rejected.append(rejected)
            else:
                report.imported.append(ImportedFile(file=_path(info)))
    return report


def _open_zip(zip_file: IO[bytes]) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(zip_file)
    except zipfile.BadZipFile as error:
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
    most, whatever size the zip states); UNSUPPORTED_MEDIA_TYPE if it is damaged (its headers or
    CRC do not match)."""
    try:
        with archive.open(info) as file:
            return read_audio_file(file)
    except zipfile.BadZipFile as error:
        message = "The file cannot be read from the zip: it is damaged"
        raise AppError(ErrorCode.UNSUPPORTED_MEDIA_TYPE, message) from error
