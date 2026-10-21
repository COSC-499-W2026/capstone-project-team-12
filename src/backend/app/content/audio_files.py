"""Checks an uploaded clue or feedback recording and removes its metadata (spec, File Handling).

A recording must be MP3 or M4A (recognised by its first bytes, not its name), at most
`max_media_file_mb` and at most `max_audio_seconds` long. The audio is copied packet by packet into
a new file that carries no tags, chapters or cover art, so an ID3 title such as "SCI333" can never
reveal an answer. Copying keeps the audio exactly as recorded (nothing is re-encoded), and the
duration is the sum of the packet durations, not an estimate from the header.

Only the first audio stream is kept: other streams (such as an MP3's embedded cover picture) are
dropped, and a file with no audio stream is rejected.
"""

import io
import math
from dataclasses import dataclass, field
from fractions import Fraction

import av
from av.container import InputContainer, OutputContainer
from av.stream import Stream

from app.config import get_settings
from app.errors import AppError, ErrorCode

_BYTES_PER_MB = 1024 * 1024


@dataclass(frozen=True)
class CheckedAudio:
    """A recording that passed the checks, ready for StorageService.save(data, suffix)."""

    data: bytes  # the copied file, without metadata
    suffix: str  # ".mp3" or ".m4a"
    duration_seconds: float
    size_kb: int  # of `data`, rounded up


@dataclass(frozen=True)
class _AudioFormat:
    suffix: str
    demuxer: str  # FFmpeg's names; forcing them stops FFmpeg guessing another format
    muxer: str
    options: dict[str, str] = field(default_factory=dict)


# bitexact: no encoder version strings; id3v2_version 0: no ID3 tag at all
_MP3 = _AudioFormat(".mp3", "mp3", "mp3", {"fflags": "+bitexact", "id3v2_version": "0"})
_M4A = _AudioFormat(".m4a", "mp4", "ipod", {"fflags": "+bitexact"})


def check_audio(data: bytes) -> CheckedAudio:
    """The recording `data` without its metadata, or AppError: 413 FILE_TOO_LARGE, 415
    UNSUPPORTED_MEDIA_TYPE (not MP3 or M4A, or unreadable) or 422 AUDIO_TOO_LONG."""
    settings = get_settings()
    if len(data) > settings.max_media_file_mb * _BYTES_PER_MB:
        raise AppError(
            ErrorCode.FILE_TOO_LARGE, f"Audio files can be at most {settings.max_media_file_mb} MB"
        )
    audio_format = _detect_format(data)
    copied, duration = _copy_without_metadata(data, audio_format)
    if duration > settings.max_audio_seconds:
        raise AppError(
            ErrorCode.AUDIO_TOO_LONG,
            f"Recordings can be at most {settings.max_audio_seconds} seconds long",
        )
    return CheckedAudio(copied, audio_format.suffix, duration, math.ceil(len(copied) / 1024))


def _unsupported() -> AppError:
    return AppError(ErrorCode.UNSUPPORTED_MEDIA_TYPE, "Upload an MP3 or M4A recording")


def _detect_format(data: bytes) -> _AudioFormat:
    # an ID3 tag, or the 11 sync bits of an MPEG audio frame (raw AAC starts with them too, but
    # FFmpeg's MP3 reader then finds no MP3 frames and rejects it)
    if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and data[1] >= 0xE0):
        return _MP3
    if data[4:8] == b"ftyp":  # an MP4 container; M4A is MP4 with only audio
        return _M4A
    raise _unsupported()


def _copy_without_metadata(data: bytes, audio_format: _AudioFormat) -> tuple[bytes, float]:
    output = io.BytesIO()
    try:
        with av.open(io.BytesIO(data), "r", format=audio_format.demuxer) as source:
            if not source.streams.audio:
                raise _unsupported()
            with av.open(
                output, "w", format=audio_format.muxer, options=audio_format.options
            ) as target:
                duration = _copy_packets(source, source.streams.audio[0], target)
    except av.FFmpegError as error:
        raise _unsupported() from error
    return output.getvalue(), duration


def _copy_packets(source: InputContainer, stream: Stream, target: OutputContainer) -> float:
    """Copy `stream` into `target`; return its length in seconds.

    Neither the new container nor the new stream gets any tags: a template copies only the codec
    settings.
    """
    copy = target.add_stream_from_template(stream)
    duration = Fraction(0)
    for packet in source.demux(stream):
        if packet.dts is None:  # the empty packet that marks the end of the stream
            continue
        if packet.duration and stream.time_base:
            duration += packet.duration * stream.time_base
        packet.stream = copy
        target.mux(packet)
    return float(duration)
