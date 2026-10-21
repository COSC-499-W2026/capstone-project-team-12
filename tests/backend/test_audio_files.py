"""Uploaded recordings are checked for format, size and length, and re-encoded without metadata."""

import io
import struct
from collections.abc import Callable
from http import HTTPStatus

import av
import pytest
from av.stream import Disposition

from app.config import get_settings
from app.content.audio_files import check_audio
from app.errors import AppError, ErrorCode

ONE_MB = 1024 * 1024
UNSUPPORTED = (ErrorCode.UNSUPPORTED_MEDIA_TYPE, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
ID3V1_TAG = b"TAG" + b"SCI333".ljust(30, b"\0") + bytes(95)  # 128 bytes after the audio
LYRICS3_TAG = b"LYRICSBEGININD0000210ETT00006SCI333000035LYRICS200"


def rejection(data: bytes) -> tuple[ErrorCode, HTTPStatus]:
    with pytest.raises(AppError) as error:
        check_audio(data)
    return error.value.code, error.value.status


def codec_of(data: bytes, suffix: str) -> str:
    with av.open(io.BytesIO(data), "r", format="mp3" if suffix == ".mp3" else "mp4") as file:
        return file.streams.audio[0].codec_context.name


@pytest.mark.parametrize(("kind", "suffix"), [("mp3", ".mp3"), ("m4a", ".m4a")])
def test_a_valid_recording_is_kept_with_its_length_and_without_its_tags(
    make_audio: Callable[..., bytes], kind: str, suffix: str
) -> None:
    original = make_audio(3, kind, title="SCI333")
    assert b"SCI333" in original  # the tag the check must remove

    checked = check_audio(original)

    assert checked.suffix == suffix
    assert checked.duration_seconds == pytest.approx(3, abs=0.05)  # AAC may keep its padding
    assert b"SCI333" not in checked.data
    assert checked.size_kb == -(-len(checked.data) // 1024)
    assert check_audio(checked.data).duration_seconds == pytest.approx(3, abs=0.05)  # it plays


def test_an_mp3_keeps_no_id3_tag(make_audio: Callable[..., bytes]) -> None:
    original = make_audio(1, title="SCI333")
    assert original.startswith(b"ID3")
    assert not check_audio(original).data.startswith(b"ID3")


@pytest.mark.parametrize(
    "appended",
    [pytest.param(LYRICS3_TAG + ID3V1_TAG, id="Lyrics3 and ID3v1 tags"), b"Room SCI333, floor 3"],
)
def test_bytes_after_the_audio_are_dropped(
    make_audio: Callable[..., bytes], appended: bytes
) -> None:
    checked = check_audio(make_audio(2) + appended)
    assert b"SCI333" not in checked.data
    assert checked.duration_seconds == 2


@pytest.mark.parametrize(
    ("kind", "suffix", "codec"),
    [
        ("alac", ".m4a", "aac"),  # lossless M4A, which Chrome and Firefox cannot play
        ("flac-in-mp4", ".m4a", "aac"),
        ("opus-in-mp4", ".m4a", "aac"),
        ("mp2", ".mp3", "mp3float"),  # MPEG audio layer 2 starts like an MP3 frame
    ],
)
def test_other_codecs_are_stored_as_mp3_or_aac(
    make_audio: Callable[..., bytes], kind: str, suffix: str, codec: str
) -> None:
    checked = check_audio(make_audio(2, kind))
    assert (checked.suffix, codec_of(checked.data, checked.suffix)) == (suffix, codec)


def test_a_tag_in_an_unknown_text_encoding_is_dropped_too(
    make_audio: Callable[..., bytes],
) -> None:
    tagged = make_audio(1, "m4a", title="SCIxx3").replace(b"SCIxx3", b"\xff\xfeSC33")  # not UTF-8
    assert b"\xff\xfeSC33" not in check_audio(tagged).data


@pytest.mark.parametrize("kind", ["mp3", "m4a"])
def test_a_recording_exactly_at_the_time_limit_is_accepted(
    make_audio: Callable[..., bytes], kind: str
) -> None:
    limit = get_settings().max_audio_seconds
    assert check_audio(make_audio(limit, kind)).duration_seconds == pytest.approx(limit, abs=0.05)


@pytest.mark.parametrize("kind", ["mp3", "m4a"])
def test_a_recording_over_the_time_limit_is_rejected(
    make_audio: Callable[..., bytes], kind: str
) -> None:
    too_long = make_audio(get_settings().max_audio_seconds + 0.1, kind)
    assert rejection(too_long) == (ErrorCode.AUDIO_TOO_LONG, HTTPStatus.UNPROCESSABLE_ENTITY)


def test_a_recording_too_short_to_store_a_length_is_rejected(
    make_audio: Callable[..., bytes],
) -> None:
    # 4 ms rounds to a length of 0.00 s, which audio_assets refuses; 6 ms rounds to 0.01 s
    assert rejection(make_audio(0.004)) == UNSUPPORTED
    assert check_audio(make_audio(0.006)).duration_seconds == 0.01


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
        pytest.param(b"\xff\xfb\x90\x00" + b"not audio " * 300, id="MP3 frame header, no audio"),
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


def mp4_video(with_audio: bool) -> bytes:
    buffer = io.BytesIO()
    with av.open(buffer, "w", format="mp4") as container:
        video = container.add_stream("mpeg4", rate=1)  # every stream is added before muxing
        video.width = video.height = 16
        audio = container.add_stream("aac", rate=48000, layout="mono") if with_audio else None
        frame = av.VideoFrame(16, 16, "yuv420p")
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        container.mux(video.encode(frame))
        container.mux(video.encode(None))
        if audio:
            sound = av.AudioFrame(format="fltp", layout="mono", samples=1024)
            sound.planes[0].update(bytes(sound.planes[0].buffer_size))
            sound.sample_rate, sound.pts = 48000, 0
            container.mux(audio.encode(sound))
            container.mux(audio.encode(None))
    return buffer.getvalue()


@pytest.mark.parametrize("with_audio", [False, True])
def test_an_mp4_video_is_rejected_with_or_without_sound(with_audio: bool) -> None:
    assert rejection(mp4_video(with_audio)) == UNSUPPORTED


def test_an_mp4_with_only_a_cover_picture_is_rejected() -> None:
    buffer = io.BytesIO()
    with av.open(buffer, "w", format="mp4") as container:
        cover = container.add_stream("png", rate=1)
        cover.width = cover.height = 16
        cover.pix_fmt = "rgb24"
        cover.disposition = Disposition.attached_pic
        picture = av.VideoFrame(16, 16, "rgb24")
        picture.planes[0].update(bytes(picture.planes[0].buffer_size))
        container.mux(cover.encode(picture))
        container.mux(cover.encode(None))
    assert rejection(buffer.getvalue()) == UNSUPPORTED


def test_an_m4a_whose_audio_does_not_decode_is_rejected(make_audio: Callable[..., bytes]) -> None:
    m4a = make_audio(1, "m4a")
    start = m4a.index(b"mdat") + 4  # the audio data, after the box's size and name
    size = int.from_bytes(m4a[start - 8 : start - 4], "big") - 8
    assert rejection(m4a[:start] + bytes(size) + m4a[start + size :]) == UNSUPPORTED


def test_an_mp3_with_a_cover_picture_is_accepted_without_it(
    make_audio: Callable[..., bytes],
) -> None:
    mp3 = make_audio(1)
    # replace the encoder's own ID3 tag, whose size is stored in 7 bits per byte
    tag_size = sum((mp3[6 + i] & 0x7F) << (7 * (3 - i)) for i in range(4))
    picture = b"\x00image/png\x00\x03\x00" + b"\x89PNG\r\n\x1a\nSCI333"  # cover art (type 3)
    frame = b"APIC" + struct.pack(">I", len(picture)) + b"\x00\x00" + picture
    size = len(frame)
    header = b"ID3\x03\x00\x00" + bytes((size >> shift) & 0x7F for shift in (21, 14, 7, 0))
    with_cover = header + frame + mp3[10 + tag_size :]

    checked = check_audio(with_cover)

    assert b"SCI333" not in checked.data
    assert checked.duration_seconds == 1
