"""Smoke test: EasyOCR runs on CPU inside the API image with its models baked in (issue #120).

The Dockerfile bakes the models into the image and sets EASYOCR_MODULE_PATH. Outside the image
that variable is unset, so these tests are skipped and CI never downloads models.
Run them in the image (from the repo root, any shell; mounting app/ tests the current code
without rebuilding the image):

    docker compose run --rm --no-deps -v ./tests:/tests:ro -v ./src/backend/app:/app/app:ro api sh

then, inside the container:

    pip install -q -r requirements-dev.txt
    pytest -c pyproject.toml /tests/backend/test_ocr_smoke.py -v
"""

import io
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.models.enums import PhotoResult
from app.verification import ocr
from app.verification.matching import MatchThresholds, decide

pytestmark = pytest.mark.skipif(
    not os.environ.get("EASYOCR_MODULE_PATH"),
    reason="OCR models are only baked into the API image (EASYOCR_MODULE_PATH is not set)",
)

PHOTO_MARGIN = 120  # white pixels around the sign in sign_photo
# Confidences of the same read may differ in the last digits when two reads share torch's threads
CONFIDENCE_TOLERANCE = 1e-3


def sign_png(text: str) -> bytes:
    """Render black text on white, like a plain room-number sign."""
    image = Image.new("RGB", (480, 160), "white")
    font = ImageFont.load_default(size=96)
    ImageDraw.Draw(image).text((30, 25), text, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture(scope="module")
def reader() -> Any:
    """The app's shared reader (downloads disabled), so the run loads the models only once."""
    return ocr._load_reader()


def test_pytorch_is_cpu_only() -> None:
    import torch

    assert torch.version.cuda is None
    assert not torch.cuda.is_available()


def test_models_are_baked_into_the_image() -> None:
    model_dir = Path(os.environ["EASYOCR_MODULE_PATH"]) / "model"
    names = {path.name for path in model_dir.glob("*.pth")}
    assert {"craft_mlt_25k.pth", "english_g2.pth"} <= names


def test_missing_models_fail_instead_of_downloading(tmp_path: Path) -> None:
    import easyocr

    with pytest.raises(FileNotFoundError, match="downloads disabled"):
        easyocr.Reader(
            ["en"], gpu=False, model_storage_directory=str(tmp_path), download_enabled=False
        )


def test_reads_room_number_sign(reader: Any) -> None:
    pieces = reader.readtext(sign_png("SCI 333"), allowlist=ocr.ALLOWLIST, detail=0)
    assert "".join(pieces).replace(" ", "").replace("-", "") == "SCI333"


def test_allowlist_blocks_other_characters(reader: Any) -> None:
    pieces = reader.readtext(sign_png("Room 12, ok?"), allowlist=ocr.ALLOWLIST, detail=0)
    assert all(set(piece) <= set(ocr.ALLOWLIST) for piece in pieces)


def sign_photo(text: str) -> Image.Image:
    """The sign inside a larger grayscale frame, as in a photo, which is what the app reads.

    EasyOCR's confidence drops on a tight crop: the bare sign "SCI 333" reads correctly but at
    about 0.49, and at about 0.97 with this margin.
    """
    sign = Image.open(io.BytesIO(sign_png(text))).convert("L")
    photo = Image.new("L", (sign.width + 2 * PHOTO_MARGIN, sign.height + 2 * PHOTO_MARGIN), 255)
    photo.paste(sign, (PHOTO_MARGIN, PHOTO_MARGIN))
    return photo


def test_app_reader_matches_the_sign_with_its_answer() -> None:
    boxes = ocr.read_text(sign_photo("SCI 333"))

    assert decide(boxes, ["SCI333"], MatchThresholds.from_settings()).result is PhotoResult.MATCH


def dim_sign_photo(text: str) -> Image.Image:
    """Gray text only 5 levels darker than its sign, below a bright window and above a dark floor.

    The window and the floor already span 0-255, so auto-contrast cannot help; the first read
    finds no room number here, and the CLAHE copy reads it at about 0.98.
    """
    photo = Image.new("L", (1200, 720), 105)
    draw = ImageDraw.Draw(photo)
    draw.rectangle((0, 0, 1200, 160), fill=255)
    draw.rectangle((0, 680, 1200, 720), fill=0)
    draw.text((360, 340), text, fill=100, font=ImageFont.load_default(size=96))
    return photo


def test_second_pass_copy_reads_a_dim_sign_the_first_read_misses() -> None:
    photo = dim_sign_photo("SCI 333")
    thresholds = MatchThresholds.from_settings()

    first = decide(ocr.read_text(photo), ["SCI333"], thresholds)
    second = decide(ocr.read_text(ocr.local_contrast(photo)), ["SCI333"], thresholds)

    assert first.result is not PhotoResult.MATCH
    assert second.result is PhotoResult.MATCH


def test_two_reads_at_once_on_the_shared_reader_match_reads_one_at_a_time() -> None:
    signs = [sign_photo(text) for text in ["SCI 333", "LIB 312", "EME 105", "ASC 140"]]
    one_at_a_time = [box for sign in signs for box in ocr.read_text(sign)]

    with ThreadPoolExecutor(max_workers=2) as pool:
        together = [box for boxes in pool.map(ocr.read_text, signs) for box in boxes]

    assert [box.text for box in together] == [box.text for box in one_at_a_time]
    assert [box.confidence for box in together] == pytest.approx(
        [box.confidence for box in one_at_a_time], abs=CONFIDENCE_TOLERANCE
    )
