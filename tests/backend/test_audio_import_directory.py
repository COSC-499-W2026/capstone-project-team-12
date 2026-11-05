"""The size of a zip's list of files (its central directory) is checked before zipfile reads
the list into memory (A20, 5.4)."""

import io
import struct
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from test_clue_audio_import import zip_of

from app.config import get_settings
from app.content.audio_import import import_clue_audio
from app.content.schemas import AudioImportReport
from app.errors import AppError, ErrorCode
from app.models.accounts import User

END, ZIP64_END, ZIP64_LOCATOR = b"PK\x05\x06", b"PK\x06\x06", b"PK\x06\x07"
ENTRY = 46  # bytes of a file's entry in the list, before its name, extra fields and comment
BYTES_PER_FILE = 4096  # allowed in the list for each file the import takes
# the extra fields macOS writes on every entry: a timestamp and the owner's user and group ids
MAC_EXTRA = struct.pack("<HHBl", 0x5455, 5, 1, 0) + struct.pack(
    "<HHBBIBI", 0x7875, 11, 1, 4, 1, 4, 1
)


@pytest.fixture
def import_zip(
    db: Session, stored_media_dir: Path, make_user: Callable[..., User]
) -> Callable[[bytes], AudioImportReport]:
    staff = make_user(role="staff", can_manage_content=True)
    return lambda data: import_clue_audio(io.BytesIO(data), staff.id, db)


@pytest.fixture
def one_file_limit(monkeypatch: pytest.MonkeyPatch, stored_media_dir: Path) -> int:
    """MAX_IMPORT_FILES 1: the list of files can be at most this many bytes."""
    monkeypatch.setenv("MAX_IMPORT_FILES", "1")
    get_settings.cache_clear()  # stored_media_dir clears it again after the test
    return BYTES_PER_FILE


def directory_size(data: bytes) -> int:
    """The list's size in bytes, as the end of the zip states it (the ZIP64 one if there is one)."""
    if (start := data.rfind(ZIP64_END)) >= 0:
        return int(struct.unpack_from("<Q", data, start + 40)[0])
    return int(struct.unpack_from("<I", data, data.rfind(END) + 12)[0])


def with_zip64_end(data: bytes, size: int) -> bytes:
    """The zip with a ZIP64 end record before its end record, stating the list's size as `size`,
    as a zip of over 65535 files or 4 GB has. zipfile then reads the ZIP64 record's size."""
    end = data.rfind(END)
    entries, _, offset = struct.unpack_from("<HII", data, end + 10)
    record = struct.pack("<4sQ2H2L4Q", ZIP64_END, 44, 45, 45, 0, 0, entries, entries, size, offset)
    locator = struct.pack("<4sLQL", ZIP64_LOCATOR, 0, end, 1)
    return data[:end] + record + locator + data[end:]


def forbid_reading_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    def read_list(self: zipfile.ZipFile) -> None:
        raise AssertionError("zipfile read the list of files")

    monkeypatch.setattr(zipfile.ZipFile, "_RealGetContents", read_list)


def assert_refused(import_zip: Callable[[bytes], AudioImportReport], data: bytes) -> None:
    with pytest.raises(AppError) as error:
        import_zip(data)

    assert error.value.code == ErrorCode.VALIDATION_ERROR
    message = "The zip lists too many files, or very long names; one import takes at most 1"
    assert error.value.details == [{"in": "body", "field": "file", "message": message}]


def test_list_longer_than_the_files_need_is_refused_before_it_is_read(
    monkeypatch: pytest.MonkeyPatch,
    one_file_limit: int,
    import_zip: Callable[[bytes], AudioImportReport],
) -> None:
    name = "x" * (one_file_limit - ENTRY - len(".mp3")) + ".mp3"
    longest = zip_of({name: b""})
    assert directory_size(longest) == one_file_limit
    assert [rejected.file for rejected in import_zip(longest).rejected] == [name]  # bad name

    too_long = zip_of({f"x{name}": b""})
    forbid_reading_the_list(monkeypatch)
    assert_refused(import_zip, too_long)


def test_size_in_a_zip64_end_record_is_the_one_checked(
    monkeypatch: pytest.MonkeyPatch,
    one_file_limit: int,
    import_zip: Callable[[bytes], AudioImportReport],
) -> None:
    data = zip_of({"x.mp3": b""})
    stated = with_zip64_end(data, directory_size(data))
    assert [rejected.file for rejected in import_zip(stated).rejected] == ["x.mp3"]

    forbid_reading_the_list(monkeypatch)
    assert_refused(import_zip, with_zip64_end(data, one_file_limit + 1))


def test_zip_of_the_most_files_made_on_a_mac_is_not_refused(
    import_zip: Callable[[bytes], AudioImportReport],
) -> None:
    limit = get_settings().max_import_files
    folder = "Japanese Scavenger Hunt/Clue recordings 2026/French (fr)/"
    clues = [f"{folder}QZX{1000 + number}_2_fr.mp3" for number in range(limit)]
    # Archive Utility adds an entry for each folder and a ._ file under __MACOSX/ for each file
    folders = ["Japanese Scavenger Hunt/", "Japanese Scavenger Hunt/Clue recordings 2026/", folder]
    resource_forks = [f"__MACOSX/{folder}._{name.rsplit('/', 1)[1]}" for name in clues]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in folders + clues + resource_forks:
            info = zipfile.ZipInfo(name)
            info.extra = MAC_EXTRA
            archive.writestr(info, b"")

    report = import_zip(buffer.getvalue())

    assert len(report.rejected) == limit  # each file, for its unknown room
