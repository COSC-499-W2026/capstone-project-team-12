"""Checks an uploaded sign photo and prepares it for storage and OCR (DFD 3.2 and 3.3).

Rules from the spec (Game Flow > Stage 2: Verification, steps 2 to 5, and File Handling):

- A photo is a JPEG or PNG of at most `max_media_file_mb`, recognised by its first bytes, never
  by its name or content type. At most one byte over the size limit is read.
- Whether the upload carries camera EXIF is recorded (`has_camera_exif`), then the photo is
  re-encoded: turned upright from its EXIF orientation, shrunk to at most 1600 px on its long
  edge (never enlarged) and saved as a JPEG without EXIF, so no embedded GPS position or device
  data is stored.
- OCR reads an enhanced copy: grayscale with auto-contrast.

Choices the spec leaves open (proposed, to be confirmed by the team): "camera EXIF" is a `Make`,
`Model` or `DateTimeOriginal` tag, which phone cameras write and in-app camera frames never carry;
a file that starts like a JPEG or PNG but does not decode, and a photo of more than
`max_photo_megapixels`, are 415 UNSUPPORTED_MEDIA_TYPE like any other file that is not a photo.
The pixel limit is checked from the file's header, before anything is decoded. EXIF that cannot
be read counts as no EXIF: it is only used for the orientation and a teacher's flag, so it never
stops a photo. Every photo is stored as a JPEG, PNG uploads included, and keeps nothing from the
upload but its pixels: no EXIF, colour profile or comment (Pillow would otherwise copy a JPEG
comment into the new file). Transparent parts are put on white, and a 16-bit PNG is scaled to 8
bits, so neither turns into a blank image that costs the student an attempt.

Memory: a JPEG is decoded at the smallest scale that still leaves the long edge to shrink, and
the photo is shrunk before any full-size copy is made. A PNG cannot be decoded at a smaller scale.
"""

import io
import math
from dataclasses import dataclass
from typing import IO

from PIL import ExifTags, Image, ImageOps

from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.services.uploads import BYTES_PER_MB, read_upload

MAX_LONG_EDGE_PX = 1600  # spec, Stage 2 step 3; the client sends photos this size already
JPEG_QUALITY = 90  # the stored photo is kept 90 days for the OCR evaluation, so keep detail
PIXELS_PER_MEGAPIXEL = 1_000_000

_FORMATS = ("JPEG", "PNG")  # Pillow's readers, so no other format is ever decoded
_SIGNATURES = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")
# What Pillow raises for a file it cannot decode: OSError for one it cannot read or that ends
# early, SyntaxError for some broken PNG chunks, ValueError for a PNG text chunk or colour profile
# that unpacks past its limit, DecompressionBombError from 179 MP (before our own limit is read)
_DECODE_ERRORS = (OSError, SyntaxError, ValueError, Image.DecompressionBombError)
# and for EXIF it cannot read: SyntaxError for a damaged header, ValueError for some damaged values
# (an offset before the start, a PNG's hex EXIF profile that is not hex)
_EXIF_ERRORS = (SyntaxError, ValueError)
_CAMERA_TAGS = (ExifTags.Base.Make, ExifTags.Base.Model)
# Modes Pillow shrinks by nearest pixel only, which blurs text badly; they are converted first
_NEAREST_ONLY = ("P", "1")
# The turn or flip that makes a photo upright, by its EXIF orientation. ImageOps.exif_transpose
# does the same but also rewrites the rest of the EXIF, which fails on a damaged tag; the EXIF is
# dropped here anyway.
_UPRIGHT = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}


@dataclass(frozen=True)
class PreparedPhoto:
    """A photo that passed the checks: the file to store and the image OCR reads."""

    data: bytes  # the re-encoded JPEG, for StorageService.save(data, ".jpg")
    enhanced: Image.Image  # grayscale, auto-contrast, the same size as `data`
    has_camera_exif: bool  # the upload carried camera EXIF (spec: the not_in_app_capture flag)


def read_photo(upload: IO[bytes]) -> bytes:
    """The bytes of an uploaded photo, or AppError: 413 FILE_TOO_LARGE over `max_media_file_mb`,
    415 UNSUPPORTED_MEDIA_TYPE if they do not start like a JPEG or PNG. Reads one byte more than
    the size limit at most."""
    max_mb = get_settings().max_media_file_mb
    data = read_upload(upload, max_mb * BYTES_PER_MB, f"Photos can be at most {max_mb} MB")
    if not data.startswith(_SIGNATURES):
        raise _unsupported()
    return data


def prepare_photo(data: bytes) -> PreparedPhoto:
    """The photo in `data` (from read_photo) re-encoded and enhanced, or AppError 415
    UNSUPPORTED_MEDIA_TYPE if it does not decode or has more than `max_photo_megapixels`."""
    image = _decode(data)
    exif = _read_exif(image)
    photo = _resizable(image)
    photo.thumbnail((MAX_LONG_EDGE_PX, MAX_LONG_EDGE_PX))  # a square box: before turning is fine
    photo = _upright(_on_white(photo), exif.get(ExifTags.Base.Orientation, 1))
    photo.info.clear()  # what the upload's metadata left; saving would write a JPEG comment
    output = io.BytesIO()
    photo.save(output, "JPEG", quality=JPEG_QUALITY)
    enhanced = ImageOps.autocontrast(photo.convert("L"))
    return PreparedPhoto(output.getvalue(), enhanced, _has_camera_exif(exif))


def _unsupported(message: str = "Upload a JPEG or PNG photo") -> AppError:
    return AppError(ErrorCode.UNSUPPORTED_MEDIA_TYPE, message)


def _decode(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data), formats=_FORMATS)
        _check_pixels(image)
        image.draft("RGB", _fitted_size(image.width, image.height))  # JPEG only
        image.load()
    except _DECODE_ERRORS as error:
        raise _unsupported() from error
    return image


def _check_pixels(image: Image.Image) -> None:
    limit = get_settings().max_photo_megapixels
    if image.width * image.height > limit * PIXELS_PER_MEGAPIXEL:
        raise _unsupported(f"Photos can have at most {limit} megapixels")


def _fitted_size(width: int, height: int) -> tuple[int, int]:
    """`width` x `height` shrunk to fit MAX_LONG_EDGE_PX, never enlarged; rounded up, so a JPEG
    decoded at a smaller scale is never smaller than the photo it is shrunk to."""
    scale = min(1, MAX_LONG_EDGE_PX / max(width, height))
    return math.ceil(width * scale), math.ceil(height * scale)


def _read_exif(image: Image.Image) -> Image.Exif:
    try:
        return image.getexif()  # after decoding: a PNG may keep its EXIF after the image data
    except _EXIF_ERRORS:
        return Image.Exif()


def _resizable(image: Image.Image) -> Image.Image:
    """`image` with 8 bits per channel, in a mode Pillow shrinks smoothly."""
    if image.mode.startswith("I"):  # a 16-bit grayscale PNG: converting would clip it to white
        return image.point(lambda value: value / 256, "L")
    if image.mode in _NEAREST_ONLY:
        return image.convert("RGBA" if image.has_transparency_data else "RGB")
    return image


def _on_white(photo: Image.Image) -> Image.Image:
    """`photo` in RGB, with transparent parts on white (converting alone would show the colour
    stored under them, often black)."""
    if not photo.has_transparency_data:
        return photo.convert("RGB")
    white = Image.new("RGBA", photo.size, "white")
    return Image.alpha_composite(white, photo.convert("RGBA")).convert("RGB")


def _upright(photo: Image.Image, orientation: int) -> Image.Image:
    turn = _UPRIGHT.get(orientation)  # 1 (upright) or a damaged value: left as it is
    return photo if turn is None else photo.transpose(turn)


def _has_camera_exif(exif: Image.Exif) -> bool:
    if any(tag in exif for tag in _CAMERA_TAGS):
        return True
    try:
        return ExifTags.Base.DateTimeOriginal in exif.get_ifd(ExifTags.IFD.Exif)
    except _EXIF_ERRORS:
        return False
