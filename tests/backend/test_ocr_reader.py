"""Unit tests for the shared OCR reader (app/verification/ocr.py).

A fake `easyocr` module stands in for the real one, so no model is loaded and the tests run
everywhere. Real reads are in test_ocr_smoke.py, which only runs inside the API image.
"""

import string
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from PIL import Image, ImageOps

from app.verification import ocr
from app.verification.matching import TextBox

BOX = [[0, 0], [10, 0], [10, 10], [0, 10]]  # EasyOCR's corner points; the app ignores them


class FakeReader:
    def __init__(self, found: list[tuple[Any, str, Any]]) -> None:
        self.found = found
        self.calls: list[tuple[np.ndarray, dict[str, Any]]] = []

    def readtext(self, image: np.ndarray, **options: Any) -> list[tuple[Any, str, Any]]:
        self.calls.append((image, options))
        return self.found


class FakeEasyOcr:
    """Takes the place of the easyocr module: records every reader it builds."""

    def __init__(self) -> None:
        self.found: list[tuple[Any, str, Any]] = []
        self.readers: list[FakeReader] = []
        self.arguments: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def Reader(self, *args: Any, **kwargs: Any) -> FakeReader:
        self.arguments.append((args, kwargs))
        self.readers.append(FakeReader(self.found))
        return self.readers[-1]


@pytest.fixture
def fake_easyocr(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeEasyOcr]:
    """A fresh fake module and no reader built yet."""
    fake = FakeEasyOcr()
    monkeypatch.setitem(sys.modules, "easyocr", fake)
    ocr._load_reader.cache_clear()
    yield fake
    ocr._load_reader.cache_clear()


def gray_image() -> Image.Image:
    image = Image.new("L", (40, 20), 255)
    image.paste(0, (5, 5, 15, 15))
    return image


def test_each_box_is_returned_with_its_confidence_in_reading_order(
    fake_easyocr: FakeEasyOcr,
) -> None:
    fake_easyocr.found = [(BOX, "SCI", np.float64(0.91)), (BOX, "333", np.float32(0.5))]

    boxes = ocr.read_text(gray_image())

    assert boxes == [TextBox("SCI", 0.91), TextBox("333", 0.5)]
    assert all(type(box.confidence) is float for box in boxes)


def test_a_photo_without_text_gives_no_boxes(fake_easyocr: FakeEasyOcr) -> None:
    assert ocr.read_text(gray_image()) == []


def test_the_grayscale_pixels_are_read_with_the_sign_characters_only(
    fake_easyocr: FakeEasyOcr,
) -> None:
    image = gray_image()

    ocr.read_text(image)

    [(pixels, options)] = fake_easyocr.readers[0].calls
    assert pixels.shape == (20, 40)  # 2-D, so EasyOCR treats it as grayscale
    assert np.array_equal(pixels, np.asarray(image))
    assert options == {"allowlist": ocr.ALLOWLIST}


@pytest.mark.parametrize("mode", ["RGB", "1", "LA"])
def test_an_image_that_is_not_grayscale_is_refused_before_reading(
    fake_easyocr: FakeEasyOcr, mode: str
) -> None:
    with pytest.raises(ValueError, match=f"not {mode}"):
        ocr.read_text(gray_image().convert(mode))

    assert fake_easyocr.readers == []


def test_the_allowlist_is_digits_capital_letters_space_and_hyphen() -> None:
    assert sorted(ocr.ALLOWLIST) == sorted(string.digits + string.ascii_uppercase + " -")


def test_one_reader_without_downloads_serves_every_read(fake_easyocr: FakeEasyOcr) -> None:
    assert fake_easyocr.readers == []  # nothing is loaded before the first read

    ocr.read_text(gray_image())
    ocr.read_text(gray_image())

    assert fake_easyocr.arguments == [((["en"],), {"gpu": False, "download_enabled": False})]


def test_reads_that_arrive_together_build_one_reader(fake_easyocr: FakeEasyOcr) -> None:
    building = threading.Semaphore(0)
    finish = threading.Event()
    build = fake_easyocr.Reader

    def slow_build(*args: Any, **kwargs: Any) -> FakeReader:
        building.release()
        finish.wait(timeout=5)
        return build(*args, **kwargs)

    fake_easyocr.Reader = slow_build
    readers = [threading.Thread(target=ocr.read_text, args=(gray_image(),)) for _ in range(2)]
    readers[0].start()
    assert building.acquire(timeout=5)
    readers[1].start()
    second_started_building = building.acquire(timeout=0.5)
    finish.set()
    for reader in readers:
        reader.join(timeout=5)

    assert not second_started_building
    assert len(fake_easyocr.readers) == 1


def dark_photo() -> Image.Image:
    """Grays 40-79 from left to right, with one black and one white pixel in a corner."""
    pixels = np.tile(np.arange(40, 80, dtype=np.uint8).repeat(4), (160, 1))
    pixels[0, :2] = (255, 0)
    return Image.fromarray(pixels)


def test_the_second_pass_copy_spreads_a_dim_area_that_auto_contrast_cannot() -> None:
    photo = dark_photo()
    assert ImageOps.autocontrast(photo) == photo  # the corner pixels already span 0-255

    second = ocr.local_contrast(photo)

    middle = (20, 20, 140, 140)
    assert (second.mode, second.size) == ("L", photo.size)
    assert np.ptp(np.asarray(second.crop(middle))) > np.ptp(np.asarray(photo.crop(middle))) + 10


def test_the_second_pass_uses_the_measured_clahe_settings() -> None:
    measured = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

    second = ocr.local_contrast(dark_photo())

    assert np.array_equal(np.asarray(second), measured.apply(np.asarray(dark_photo())))


@pytest.mark.parametrize("mode", ["RGB", "1", "LA"])
def test_the_second_pass_copy_needs_a_grayscale_image(mode: str) -> None:
    with pytest.raises(ValueError, match=f"not {mode}"):
        ocr.local_contrast(gray_image().convert(mode))


def test_importing_the_module_loads_neither_easyocr_nor_torch() -> None:
    backend = Path(ocr.__file__).parents[2]
    check = (
        "import sys, app.main, app.verification.ocr; "
        "sys.exit(not {'easyocr', 'torch'}.isdisjoint(sys.modules))"
    )
    assert subprocess.run([sys.executable, "-c", check], cwd=backend).returncode == 0
