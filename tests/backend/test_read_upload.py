import io

import pytest

from app.errors import AppError, ErrorCode
from app.services.uploads import read_upload

LIMIT = 10


@pytest.mark.parametrize("size", [0, 1, LIMIT])
def test_file_up_to_the_limit_is_returned_whole(size: int) -> None:
    data = bytes(range(size))

    assert read_upload(io.BytesIO(data), LIMIT, "too large") == data


def test_file_one_byte_over_the_limit_is_refused_with_the_callers_message() -> None:
    with pytest.raises(AppError, match="Photos can be at most 10 bytes") as raised:
        read_upload(io.BytesIO(b"x" * (LIMIT + 1)), LIMIT, "Photos can be at most 10 bytes")

    assert raised.value.code == ErrorCode.FILE_TOO_LARGE


def test_large_file_is_read_no_further_than_one_byte_past_the_limit() -> None:
    upload = io.BytesIO(b"x" * (LIMIT * 100))

    with pytest.raises(AppError):
        read_upload(upload, LIMIT, "too large")

    assert upload.tell() == LIMIT + 1
