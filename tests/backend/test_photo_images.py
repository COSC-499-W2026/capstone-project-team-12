"""Uploaded photos are checked for size and type, re-encoded without EXIF and enhanced for OCR."""

import io
import struct
import zlib
from http import HTTPStatus

import pytest
from PIL import ExifTags, Image, ImageDraw, ImageOps, PngImagePlugin

from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.verification import images
from app.verification.images import PreparedPhoto, prepare_photo, read_photo

ONE_MB = 1024 * 1024
UNSUPPORTED = (ErrorCode.UNSUPPORTED_MEDIA_TYPE, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
JPEG_START = b"\xff\xd8\xff"


def encode(image: Image.Image, image_format: str = "JPEG", **options: object) -> bytes:
    output = io.BytesIO()
    image.save(output, image_format, **options)
    return output.getvalue()


def photo(
    size: tuple[int, int] = (400, 300), image_format: str = "JPEG", **options: object
) -> bytes:
    return encode(Image.new("RGB", size, "white"), image_format, **options)


def exif_with(*tags: int) -> Image.Exif:
    exif = Image.Exif()
    for tag in tags:
        if tag == ExifTags.Base.DateTimeOriginal:  # kept in the Exif sub-IFD, not the main one
            exif.get_ifd(ExifTags.IFD.Exif)[tag] = "2026:11:11 10:00:00"
        else:
            exif[tag] = "Apple" if tag == ExifTags.Base.Make else "iPhone 15"
    return exif


def prepare(data: bytes) -> PreparedPhoto:
    return prepare_photo(read_photo(io.BytesIO(data)))


def stored(prepared: PreparedPhoto) -> Image.Image:
    return Image.open(io.BytesIO(prepared.data))


def rejection(data: bytes) -> tuple[ErrorCode, HTTPStatus]:
    with pytest.raises(AppError) as error:
        prepare(data)
    return error.value.code, error.value.status


def png_with_a_broken_chunk() -> bytes:
    """A PNG whose second image data chunk has a type that is not a chunk name (Pillow raises
    SyntaxError for it, not OSError)."""
    data = encode(Image.effect_noise((400, 400), 60), "PNG")  # noise: three IDAT chunks
    second = data.index(b"IDAT", data.index(b"IDAT") + 1)
    return data[:second] + b"\x9b\x9b\x0c\x0c" + data[second + 4 :]


def png_with_a_huge_text_chunk() -> bytes:
    """A 3 KB PNG whose compressed comment unpacks to 3 MB, past Pillow's limit (ValueError)."""
    text = PngImagePlugin.PngInfo()
    text.add_text("Comment", "A" * 3_000_000, zip=True)
    return photo(image_format="PNG", pnginfo=text)


def jpeg_with_an_exif_pointer_before_its_start() -> bytes:
    """A JPEG turned by EXIF whose pointer to the Exif sub-IFD (DateTimeOriginal) is -16."""
    exif = exif_with(ExifTags.Base.DateTimeOriginal)
    exif[ExifTags.Base.Orientation] = 6
    data = exif.tobytes()
    pointer = data.index(struct.pack(">HHI", ExifTags.IFD.Exif, 4, 1))  # the tag, LONG, 1 value
    signed = struct.pack(">HHIi", ExifTags.IFD.Exif, 9, 1, -16)  # SLONG
    damaged = data[:pointer] + signed + data[pointer + 12 :]
    return encode(Image.new("RGB", (400, 200), "white"), exif=damaged)


def png_with_exif_hex_that_is_not_hex() -> bytes:
    """A PNG with its EXIF as a hex text profile, the way older software writes it."""
    text = PngImagePlugin.PngInfo()
    text.add_text("Raw profile type exif", "\nexif\n 10\n4578696600zz\n")
    return photo((400, 200), "PNG", pnginfo=text)


def png_with_a_damaged_exif_header() -> bytes:
    """A PNG with camera EXIF whose byte order mark ("MM") is broken (Pillow reads a JPEG's
    without complaint, but raises SyntaxError for a PNG's)."""
    data = exif_with(ExifTags.Base.Make).tobytes()
    assert data[6:8] == b"MM"
    return photo((400, 200), "PNG", exif=data[:6] + b"MX" + data[8:])


def transparent_palette_image() -> Image.Image:
    """Left half palette entry 0, transparent; right half entry 1, the same dark colour, opaque."""
    image = Image.new("P", (80, 60), 0)
    image.putpalette([10, 10, 10] * 256)
    image.info["transparency"] = 0
    ImageDraw.Draw(image).rectangle((40, 0, 79, 59), fill=1)
    return image


def png_declaring(width: int, height: int) -> bytes:
    """A small PNG whose header says it is `width` x `height` pixels."""
    data = bytearray(photo((10, 10), "PNG"))
    ihdr = data.index(b"IHDR")  # chunk type, 13 bytes of data, then the CRC of type and data
    data[ihdr + 4 : ihdr + 12] = struct.pack(">II", width, height)
    data[ihdr + 17 : ihdr + 21] = struct.pack(">I", zlib.crc32(data[ihdr : ihdr + 17]))
    return bytes(data)


@pytest.mark.parametrize("image_format", ["JPEG", "PNG"])
def test_a_jpeg_or_png_photo_is_stored_as_a_jpeg_of_the_same_size(image_format: str) -> None:
    prepared = prepare(photo((400, 300), image_format))

    assert prepared.data.startswith(JPEG_START)
    assert (stored(prepared).format, stored(prepared).size) == ("JPEG", (400, 300))
    assert not prepared.has_camera_exif


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((4000, 3000), (1600, 1200)),
        ((3000, 4000), (1200, 1600)),
        ((1600, 900), (1600, 900)),
        ((800, 600), (800, 600)),  # never enlarged
    ],
)
def test_the_long_edge_is_shrunk_to_the_limit(
    size: tuple[int, int], expected: tuple[int, int]
) -> None:
    prepared = prepare(photo(size))
    assert stored(prepared).size == prepared.enhanced.size == expected


@pytest.mark.parametrize("image_format", ["JPEG", "PNG"])
def test_exif_and_comments_are_not_stored(image_format: str) -> None:
    exif = exif_with(ExifTags.Base.Make, ExifTags.Base.Model)
    exif.get_ifd(ExifTags.IFD.GPSInfo)[ExifTags.GPS.GPSLatitude] = (49.0, 56.0, 23.0)
    comment = {"comment": b"Room SCI333"} if image_format == "JPEG" else {}
    original = photo((400, 300), image_format, exif=exif.tobytes(), **comment)
    assert b"Apple" in original

    prepared = prepare(original)

    assert prepared.has_camera_exif
    assert not stored(prepared).getexif()
    assert "comment" not in stored(prepared).info
    assert b"Apple" not in prepared.data and b"SCI333" not in prepared.data


@pytest.mark.parametrize(
    ("tags", "camera"),
    [
        pytest.param((ExifTags.Base.Make,), True, id="make"),
        pytest.param((ExifTags.Base.Model,), True, id="model"),
        pytest.param((ExifTags.Base.DateTimeOriginal,), True, id="time taken"),
        pytest.param((ExifTags.Base.Orientation,), False, id="orientation only"),
        pytest.param((), False, id="no tags"),
    ],
)
@pytest.mark.parametrize("image_format", ["JPEG", "PNG"])
def test_camera_exif_is_recorded(tags: tuple[int, ...], camera: bool, image_format: str) -> None:
    exif = exif_with(*(tag for tag in tags if tag != ExifTags.Base.Orientation))
    if ExifTags.Base.Orientation in tags:
        exif[ExifTags.Base.Orientation] = 1
    assert prepare(photo((40, 30), image_format, exif=exif.tobytes())).has_camera_exif == camera


def quadrants(image: Image.Image) -> list[bool]:
    """Which quarters of the image are dark, to compare orientations despite JPEG noise."""
    corners = image.convert("L").resize((2, 2), Image.Resampling.BOX)
    return [value < 128 for value in corners.get_flattened_data()]


@pytest.mark.parametrize("orientation", range(1, 9))
def test_the_photo_is_turned_upright_from_its_exif_orientation(orientation: int) -> None:
    image = Image.new("RGB", (400, 200), "white")
    ImageDraw.Draw(image).rectangle((0, 0, 199, 99), fill="black")  # the top left quarter
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = orientation
    original = encode(image, exif=exif.tobytes())
    upright = ImageOps.exif_transpose(Image.open(io.BytesIO(original)))  # Pillow's own rule

    result = stored(prepare(original))

    assert result.size == upright.size
    assert quadrants(result) == quadrants(upright)
    assert ExifTags.Base.Orientation not in result.getexif()


def test_a_damaged_exif_tag_does_not_stop_the_photo() -> None:
    exif = exif_with(ExifTags.Base.Make, ExifTags.Base.Model)
    exif[ExifTags.Base.Orientation] = 6
    model = struct.pack(">HH", ExifTags.Base.Model, 2)  # the tag and its type, text
    exif_bytes = exif.tobytes()
    assert exif_bytes.startswith(b"Exif\x00\x00MM") and model in exif_bytes  # big-endian
    # tag 334 holds a number, so its text value cannot be written back
    damaged = photo((400, 200), exif=exif_bytes.replace(model, struct.pack(">HH", 334, 2)))
    with pytest.raises(struct.error):
        ImageOps.exif_transpose(Image.open(io.BytesIO(damaged)))  # why images.py turns it itself

    prepared = prepare(damaged)

    assert stored(prepared).size == (200, 400)  # still turned
    assert prepared.has_camera_exif


@pytest.mark.parametrize(
    ("data", "size"),
    [
        # the orientation is still read; only the sub-IFD with the time taken is lost
        pytest.param(jpeg_with_an_exif_pointer_before_its_start(), (200, 400), id="bad pointer"),
        pytest.param(png_with_exif_hex_that_is_not_hex(), (400, 200), id="bad hex profile"),
        pytest.param(png_with_a_damaged_exif_header(), (400, 200), id="bad header"),
    ],
)
def test_exif_that_cannot_be_read_counts_as_none(data: bytes, size: tuple[int, int]) -> None:
    with pytest.raises((SyntaxError, ValueError)):  # Pillow cannot read all of it
        Image.open(io.BytesIO(data)).getexif().get_ifd(ExifTags.IFD.Exif)

    prepared = prepare(data)

    assert stored(prepared).size == size
    assert not prepared.has_camera_exif


@pytest.mark.parametrize(
    ("image", "gray"),
    [
        pytest.param(Image.new("I;16", (40, 30), 1000), 3, id="16-bit dark"),
        pytest.param(Image.new("I;16", (40, 30), 60000), 234, id="16-bit light"),
        pytest.param(Image.new("RGBA", (40, 30), (0, 0, 0, 0)), 255, id="transparent RGBA"),
        pytest.param(Image.new("LA", (40, 30), (0, 0)), 255, id="transparent LA"),
        pytest.param(Image.new("RGBA", (40, 30), (20, 20, 20, 255)), 20, id="opaque RGBA"),
    ],
)
def test_a_png_keeps_its_tones_and_transparency_is_white(image: Image.Image, gray: int) -> None:
    # converting alone would clip 16 bits to white and show black under transparent parts
    assert stored(prepare(encode(image, "PNG"))).convert("L").getextrema() == (gray, gray)


def test_only_the_transparent_palette_entry_is_white() -> None:
    # converting the palette to RGB first would make every pixel of that colour transparent
    result = stored(prepare(encode(transparent_palette_image(), "PNG"))).convert("L")
    assert (result.getpixel((10, 30)), result.getpixel((70, 30))) == (255, 10)


@pytest.mark.parametrize("mode", ["P", "1"])
def test_a_palette_or_1_bit_png_is_shrunk_smoothly(mode: str) -> None:
    # Pillow shrinks these modes by nearest pixel only, which turns 1 px stripes all black
    stripes = Image.new(mode, (3200, 100), 0)
    if mode == "P":
        stripes.putpalette([0, 0, 0, 255, 255, 255])
    draw = ImageDraw.Draw(stripes)
    for x in range(0, 3200, 2):
        draw.line((x, 0, x, 99), fill=1)

    low, high = stored(prepare(encode(stripes, "PNG"))).convert("L").getextrema()

    assert low > 64 and high < 192  # gray


@pytest.mark.parametrize(
    ("size", "decoded"),
    [((4032, 3024), (2016, 1512)), ((3200, 2400), (1600, 1200)), ((3000, 2000), (3000, 2000))],
)
def test_a_large_jpeg_is_decoded_at_a_smaller_scale(
    size: tuple[int, int], decoded: tuple[int, int]
) -> None:
    # memory only: the result is never smaller than the photo it is shrunk to (1600 px long)
    assert images._decode(photo(size)).size == decoded


def test_ocr_reads_a_grayscale_copy_with_full_contrast() -> None:
    gradient = Image.linear_gradient("L").resize((400, 300)).point(lambda value: 100 + value // 5)
    assert gradient.getextrema() == (100, 151)

    enhanced = prepare(encode(gradient.convert("RGB"))).enhanced

    assert enhanced.mode == "L"
    assert enhanced.getextrema() == (0, 255)


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(photo(image_format="GIF"), id="gif"),
        pytest.param(photo(image_format="BMP"), id="bmp"),
        pytest.param(b"\x00\x00\x00\x18ftypheic" + bytes(500), id="heic"),
        pytest.param(b"Room SCI 333, second floor\n" * 20, id="text named .jpg"),
        pytest.param(b"", id="empty"),
    ],
)
def test_a_file_that_is_not_a_jpeg_or_png_is_rejected(data: bytes) -> None:
    with pytest.raises(AppError) as error:
        read_photo(io.BytesIO(data))
    assert (error.value.code, error.value.status) == UNSUPPORTED


def test_only_jpeg_and_png_are_decoded_even_without_the_signature_check() -> None:
    with pytest.raises(AppError) as error:
        prepare_photo(photo(image_format="GIF"))
    assert (error.value.code, error.value.status) == UNSUPPORTED


def test_a_photo_at_the_size_limit_is_read_and_one_byte_more_is_not() -> None:
    limit = get_settings().max_media_file_mb * ONE_MB
    at_limit = JPEG_START + bytes(limit - len(JPEG_START))
    assert read_photo(io.BytesIO(at_limit)) == at_limit

    too_big = io.BytesIO(at_limit + bytes(ONE_MB))
    with pytest.raises(AppError) as error:
        read_photo(too_big)
    assert (error.value.code, error.value.status) == (
        ErrorCode.FILE_TOO_LARGE,
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    )
    assert too_big.tell() == limit + 1  # one byte past the limit, not the rest


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(JPEG_START + b"not a photo" * 50, id="jpeg start, no image"),
        pytest.param(photo()[:400], id="jpeg cut short"),
        pytest.param(photo(image_format="PNG")[:100], id="png cut short"),
        pytest.param(b"\x89PNG\r\n\x1a\n" + bytes(200), id="png start, no image"),
        pytest.param(png_with_a_broken_chunk(), id="png with a broken chunk"),
        pytest.param(png_with_a_huge_text_chunk(), id="png text chunk too large"),
    ],
)
def test_a_file_that_starts_like_a_photo_but_does_not_decode_is_rejected(data: bytes) -> None:
    assert rejection(data) == UNSUPPORTED


def test_the_pixel_limit_comes_from_the_settings(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    monkeypatch.setenv("MAX_PHOTO_MEGAPIXELS", "2")
    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)  # the next caller reads the restored env
    assert stored(prepare(photo((2000, 1000)))).size == (1600, 800)  # exactly 2 megapixels

    with pytest.raises(AppError) as error:
        prepare(photo((2000, 1001)))
    assert (error.value.code, error.value.status, error.value.message) == (
        *UNSUPPORTED,
        "Photos can have at most 2 megapixels",
    )


@pytest.mark.parametrize(
    ("width", "height", "message"),
    [
        pytest.param(5000, 5001, "Photos can have at most 25 megapixels", id="just over the limit"),
        # over Pillow's own limit, which it refuses as a decompression bomb when opening
        pytest.param(20000, 20000, "Upload a JPEG or PNG photo", id="decompression bomb"),
    ],
)
def test_a_small_file_declaring_a_huge_image_is_rejected(
    width: int, height: int, message: str
) -> None:
    with pytest.raises(AppError) as error:
        prepare(png_declaring(width, height))
    assert (error.value.code, error.value.status, error.value.message) == (*UNSUPPORTED, message)
