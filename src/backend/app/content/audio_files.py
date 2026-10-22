"""Checks an uploaded clue or feedback recording and re-encodes it (spec, File Handling).

A recording must be MP3 or M4A (recognised by its first bytes, not its name), at most
`max_media_file_mb` and at most `max_audio_seconds` long. An MP3 file must hold MPEG layer III
audio (not layer I or II) and an M4A an MP4 audio file of a M4A brand holding AAC or ALAC (not a
.mov or .3gp, nor FLAC or Opus in MP4). At most one byte over the size limit is read. Its first
audio stream is decoded and encoded again into a new file, so nothing but the sound survives: no
tags, chapters, cover art or bytes appended after the audio (such as ID3v1 or Lyrics3 tags), so a
title such as "SCI333" can never reveal an answer. Packets that do not decode as audio (such as
those appended bytes) are skipped. An MP3 stays MP3; an M4A is always stored as AAC, which every
browser can play (an ALAC M4A could not be played in Chrome or Firefox). The length is counted
from the decoded samples, which leave out the silence encoders add when the file records it (MP3
encoder headers, most M4A files); up to 50 ms of unrecorded padding is allowed over the limit.

A file with a video track is rejected (only an MP3's embedded cover picture is allowed, and
dropped), as is a file without any audio that decodes. A recording under 5 ms is too short to
store a length (VALIDATION_ERROR: it is MP3 or M4A audio, so not UNSUPPORTED_MEDIA_TYPE).
"""

import io
import math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import BinaryIO, cast

import av
from av.audio.stream import AudioStream
from av.container import InputContainer, OutputContainer
from av.stream import Disposition

from app.config import get_settings
from app.errors import AppError, ErrorCode

_BYTES_PER_MB = 1024 * 1024
# what every stored recording is encoded as: enough for speech, playable in every browser
_SAMPLE_RATE = 44100
_LAYOUT = "mono"
_BIT_RATE = 96_000
# Silence an encoder adds at the start of the audio (about one frame, 21-26 ms) is decoded too
# when the file does not record it (some AAC files, including the ones written here). Allowing
# this much over the limit keeps a recording of exactly the limit acceptable.
_PADDING_ALLOWANCE_SECONDS = Fraction(1, 20)


@dataclass(frozen=True)
class CheckedAudio:
    """A recording that passed the checks, ready for StorageService.save(data, suffix)."""

    data: bytes  # the re-encoded file, without metadata
    suffix: str  # ".mp3" or ".m4a"
    duration_seconds: float  # to the nearest 10 ms
    size_kb: int  # of `data`, rounded up


@dataclass(frozen=True)
class _AudioFormat:
    suffix: str
    demuxer: str  # FFmpeg's names; forcing the demuxer stops FFmpeg guessing another format
    muxer: str
    encoder: str
    codecs: frozenset[str]  # the decoders of the audio an upload of this format may hold
    options: dict[str, str] = field(default_factory=dict)


# bitexact: no encoder version strings; id3v2_version 0: no ID3 tag at all
_MP3 = _AudioFormat(
    ".mp3", "mp3", "mp3", "libmp3lame", frozenset({"mp3", "mp3float"}), {"id3v2_version": "0"}
)
_M4A = _AudioFormat(".m4a", "mp4", "ipod", "aac", frozenset({"aac", "alac"}))
# the major brands of M4A files; other ISO media files (.mov "qt  ", .3gp "3gp4") are not M4A
_M4A_BRANDS = frozenset({b"M4A ", b"M4B ", b"mp41", b"mp42", b"isom", b"iso2"})


def check_audio(upload: BinaryIO) -> CheckedAudio:
    """The recording read from `upload` re-encoded without its metadata, or AppError:
    413 FILE_TOO_LARGE, 415 UNSUPPORTED_MEDIA_TYPE (not MP3 or M4A audio, or unreadable) or
    422 AUDIO_TOO_LONG. Reads one byte more than the size limit at most."""
    max_mb = get_settings().max_media_file_mb
    data = upload.read(max_mb * _BYTES_PER_MB + 1)
    if len(data) > max_mb * _BYTES_PER_MB:
        raise AppError(ErrorCode.FILE_TOO_LARGE, f"Audio files can be at most {max_mb} MB")
    audio_format = _detect_format(data)
    encoded, duration = _reencode(data, audio_format)
    return CheckedAudio(encoded, audio_format.suffix, duration, math.ceil(len(encoded) / 1024))


def _unsupported(message: str = "Upload an MP3 or M4A recording") -> AppError:
    return AppError(ErrorCode.UNSUPPORTED_MEDIA_TYPE, message)


def _detect_format(data: bytes) -> _AudioFormat:
    # an ID3 tag, or the 11 sync bits of an MPEG audio frame (raw AAC starts with them too, but
    # FFmpeg's MP3 reader then finds no MP3 frames and rejects it)
    if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and data[1] >= 0xE0):
        return _MP3
    if data[4:8] == b"ftyp" and data[8:12] in _M4A_BRANDS:  # M4A is MP4 with only audio
        return _M4A
    raise _unsupported()


def _reencode(data: bytes, audio_format: _AudioFormat) -> tuple[bytes, float]:
    output = io.BytesIO()
    try:
        # tags are never kept, so tags in an unknown text encoding must not fail the upload
        with av.open(
            io.BytesIO(data), "r", format=audio_format.demuxer, metadata_errors="ignore"
        ) as source:
            stream = _only_audio_stream(source, audio_format)
            options = {"fflags": "+bitexact", **audio_format.options}
            with av.open(output, "w", format=audio_format.muxer, options=options) as target:
                duration = _transcode(source, stream, target, audio_format.encoder)
    except av.FFmpegError as error:
        raise _unsupported() from error
    return output.getvalue(), duration


def _only_audio_stream(source: InputContainer, audio_format: _AudioFormat) -> AudioStream:
    if any(not video.disposition & Disposition.attached_pic for video in source.streams.video):
        raise _unsupported("Upload an audio recording, not a video")
    if not source.streams.audio:
        raise _unsupported()
    stream = source.streams.audio[0]
    if stream.codec_context.name not in audio_format.codecs:  # e.g. MPEG layer II in an MP3
        raise _unsupported()
    return stream


def _transcode(
    source: InputContainer, stream: AudioStream, target: OutputContainer, encoder: str
) -> float:
    """Encode the decoded `stream` into `target`; return its length in seconds.

    AppError AUDIO_TOO_LONG as soon as the length passes the limit, so a long file is not decoded
    to the end. The new container and stream get no tags.
    """
    limit = get_settings().max_audio_seconds
    # typed as any kind of stream because the encoder is named by a variable; ours are audio
    encoded = cast(AudioStream, target.add_stream(encoder, rate=_SAMPLE_RATE, layout=_LAYOUT))
    encoded.bit_rate = _BIT_RATE
    resampler = av.AudioResampler(
        format=encoded.codec_context.format.name, layout=_LAYOUT, rate=_SAMPLE_RATE
    )
    samples_seconds = Fraction(0)
    for packet in source.demux(stream):
        try:
            frames = packet.decode()
        except av.InvalidDataError:  # not audio, e.g. a tag appended after the last frame
            continue
        for frame in frames:
            samples_seconds += Fraction(frame.samples, frame.sample_rate)
            if samples_seconds > limit + _PADDING_ALLOWANCE_SECONDS:
                raise AppError(
                    ErrorCode.AUDIO_TOO_LONG, f"Recordings can be at most {limit} seconds long"
                )
            for resampled in resampler.resample(frame):
                target.mux(encoded.encode(resampled))
    if not samples_seconds:
        raise _unsupported()
    duration = round(float(samples_seconds), 2)
    if not duration:  # under 5 ms: audio_assets needs a length above 0
        message = "The recording is too short"
        details = [{"in": "body", "field": "file", "message": message}]
        raise AppError(ErrorCode.VALIDATION_ERROR, message, details)
    for resampled in resampler.resample(None):
        target.mux(encoded.encode(resampled))
    target.mux(encoded.encode(None))
    return duration
