"""Uploaded recordings are checked for format, size and length, and lose their metadata."""

import io
from collections.abc import Callable
from http import HTTPStatus

import av
import pytest

from app.config import get_settings
from app.content.audio_files import check_audio
from app.errors import AppError, ErrorCode

ONE_MB = 1024 * 1024
UNSUPPORTED = (ErrorCode.UNSUPPORTED_MEDIA_TYPE, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)


def rejection(data: bytes) -> tuple[ErrorCode, HTTPStatus]:
    with pytest.raises(AppError) as error:
        check_audio(data)
    return error.value.code, error.value.status


@pytest.mark.parametrize(("kind", "suffix"), [("mp3", ".mp3"), ("m4a", ".m4a")])
def test_a_valid_recording_is_kept_with_its_length_and_without_its_tags(
    make_audio: Callable[..., bytes], kind: str, suffix: str
) -> None:
    original = make_audio(3, kind, title="SCI333")
    assert b"SCI333" in original  # the tag the check must remove

    checked = check_audio(original)

    assert checked.suffix == suffix
    assert checked.duration_seconds == pytest.approx(3, abs=0.1)
    assert b"SCI333" not in checked.data
    assert checked.size_kb == -(-len(checked.data) // 1024)
    assert check_audio(checked.data).duration_seconds == checked.duration_seconds  # still playable


def test_an_mp3_keeps_no_id3_tag(make_audio: Callable[..., bytes]) -> None:
    original = make_audio(1, title="SCI333")
    assert original.startswith(b"ID3")
    assert not check_audio(original).data.startswith(b"ID3")


def test_a_recording_just_under_the_time_limit_is_accepted(
    make_audio: Callable[..., bytes],
) -> None:
    limit = get_settings().max_audio_seconds
    assert check_audio(make_audio(limit - 1)).duration_seconds < limit


def test_a_recording_over_the_time_limit_is_rejected(make_audio: Callable[..., bytes]) -> None:
    too_long = make_audio(get_settings().max_audio_seconds + 1)
    assert rejection(too_long) == (ErrorCode.AUDIO_TOO_LONG, HTTPStatus.UNPROCESSABLE_ENTITY)


def test_a_file_over_the_size_limit_is_rejected_before_it_is_read(
    make_audio: Callable[..., bytes],
) -> None:
    too_big = make_audio(1) + bytes(get_settings().max_media_file_mb * ONE_MB)
    assert rejection(too_big) == (ErrorCode.FILE_TOO_LARGE, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)


PNG = b"\x89PNG\r\n\x1a\n" + bytes(200)


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(PNG, id="png"),
        pytest.param(b"Room SCI 333, second floor\n" * 20, id="text named .mp3"),
        pytest.param(b"ID3" + bytes(500), id="ID3 header with no audio"),
        pytest.param(b"\x00\x00\x00\x18ftypM4A " + bytes(500), id="M4A header with no audio"),
        pytest.param(b"", id="empty"),
    ],
)
def test_a_file_that_is_not_mp3_or_m4a_audio_is_rejected(data: bytes) -> None:
    assert rejection(data) == UNSUPPORTED


def test_raw_aac_is_rejected_although_it_starts_like_an_mp3_frame(
    make_audio: Callable[..., bytes],
) -> None:
    raw_aac = make_audio(1, "adts")
    assert raw_aac[0] == 0xFF  # the same sync bits as an MP3 frame
    assert rejection(raw_aac) == UNSUPPORTED


def test_an_mp4_video_without_audio_is_rejected() -> None:
    buffer = io.BytesIO()
    with av.open(buffer, "w", format="mp4") as container:
        stream = container.add_stream("mpeg4", rate=1)
        stream.width = stream.height = 16
        frame = av.VideoFrame(16, 16, "yuv420p")
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        container.mux(stream.encode(frame))
        container.mux(stream.encode(None))
    assert rejection(buffer.getvalue()) == UNSUPPORTED
