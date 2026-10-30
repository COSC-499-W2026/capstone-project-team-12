"""Reading uploaded files (A20): every content upload is at most `max_media_file_mb`."""

from typing import BinaryIO

from app.config import get_settings
from app.errors import AppError, ErrorCode

_BYTES_PER_MB = 1024 * 1024


def read_upload(upload: BinaryIO, kind: str) -> bytes:
    """The bytes of `upload`, or 413 FILE_TOO_LARGE over `max_media_file_mb` (`kind` names the
    file in the message, e.g. "Audio files"). Reads one byte more than the limit at most."""
    max_mb = get_settings().max_media_file_mb
    data = upload.read(max_mb * _BYTES_PER_MB + 1)
    if len(data) > max_mb * _BYTES_PER_MB:
        raise AppError(ErrorCode.FILE_TOO_LARGE, f"{kind} can be at most {max_mb} MB")
    return data
