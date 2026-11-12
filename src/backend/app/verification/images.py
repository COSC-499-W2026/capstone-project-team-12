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
`MAX_PHOTO_PIXELS`, are 415 UNSUPPORTED_MEDIA_TYPE like any other file that is not a photo. The
pixel limit is checked from the file's header, before anything is decoded, so a small file that
declares a huge image cannot fill the server's memory. Every photo is stored as a JPEG, PNG
uploads included, and keeps nothing from the upload but its pixels: no EXIF, colour profile or
comment (Pillow would otherwise copy a JPEG comment into the new file).
"""

import io
from dataclasses import dataclass
from typing import IO

from PIL import ExifTags, Image, ImageOps

from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.services.uploads import BYTES_PER_MB, read_upload

MAX_LONG_EDGE_PX = 1600  # spec, Stage 2 step 3; the client sends photos this size already
# a 50-megapixel phone photo (8160 x 6120) fits; Pillow itself only refuses images from 89 MP
MAX_PHOTO_PIXELS = 50_000_000
JPEG_QUALITY = 90  # the stored photo is kept 90 days for the OCR evaluation, so keep detail

_TOO_MANY_PIXELS = f"Photos can have at most {MAX_PHOTO_PIXELS // 1_000_000} megapixels"
_FORMATS = ("JPEG", "PNG")  # Pillow's readers, so no other format is ever decoded
_SIGNATURES = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")
_CAMERA_TAGS = (ExifTags.Base.Make, ExifTags.Base.Model)
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
    UNSUPPORTED_MEDIA_TYPE if it does not decode or has more than MAX_PHOTO_PIXELS."""
    image = _decode(data)
    exif = image.getexif()
    photo = image.convert("RGB")
    photo.thumbnail((MAX_LONG_EDGE_PX, MAX_LONG_EDGE_PX))  # a square box: before turning is fine
    photo = _upright(photo, exif.get(ExifTags.Base.Orientation, 1))
    photo.info.clear()  # what the upload's metadata left; saving would write a JPEG comment
    output = io.BytesIO()
    photo.save(output, "JPEG", quality=JPEG_QUALITY)
    enhanced = ImageOps.autocontrast(photo.convert("L"))
    return PreparedPhoto(output.getvalue(), enhanced, _has_camera_exif(exif))


def _unsupported(message: str = "Upload a JPEG or PNG photo") -> AppError:
    return AppError(ErrorCode.UNSUPPORTED_MEDIA_TYPE, message)


def _decode(data: bytes) -> Image.Image:
    # Pillow raises OSError for a file it cannot read or that ends early, SyntaxError for some
    # broken PNG chunks, and DecompressionBombError from 179 MP, before MAX_PHOTO_PIXELS is checked
    try:
        image = Image.open(io.BytesIO(data), formats=_FORMATS)
        if image.width * image.height > MAX_PHOTO_PIXELS:
            raise _unsupported(_TOO_MANY_PIXELS)
        # a JPEG is decoded at a smaller scale when that still leaves the long edge to shrink to
        image.draft("RGB", (MAX_LONG_EDGE_PX, MAX_LONG_EDGE_PX))
        image.load()
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise _unsupported() from error
    return image


def _upright(photo: Image.Image, orientation: int) -> Image.Image:
    turn = _UPRIGHT.get(orientation)  # 1 (upright) or a damaged value: left as it is
    return photo if turn is None else photo.transpose(turn)


def _has_camera_exif(exif: Image.Exif) -> bool:
    taken_at = ExifTags.Base.DateTimeOriginal in exif.get_ifd(ExifTags.IFD.Exif)
    return taken_at or any(tag in exif for tag in _CAMERA_TAGS)
