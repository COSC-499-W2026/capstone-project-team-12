"""Reading uploaded files (A20) with a size limit each caller sets."""

from typing import BinaryIO

from app.errors import AppError, ErrorCode

BYTES_PER_KB = 1024
BYTES_PER_MB = 1024 * BYTES_PER_KB


def read_upload(upload: BinaryIO, max_bytes: int, too_large: str) -> bytes:
    """The bytes of `upload`, or 413 FILE_TOO_LARGE with the message `too_large` if it has more
    than `max_bytes`. Reads one byte more than the limit at most."""
    data = upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise AppError(ErrorCode.FILE_TOO_LARGE, too_large)
    return data
