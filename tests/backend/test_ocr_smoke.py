"""Smoke test: EasyOCR runs on CPU inside the API image with its models baked in (issue #120).

The Dockerfile bakes the models into the image and sets EASYOCR_MODULE_PATH. Outside the image
that variable is unset, so these tests are skipped and CI never downloads models.
Run them in the image (from the repo root):

        docker compose run --rm --no-deps -v ./tests:/tests:ro api sh -c 
        "pip install -q -r requirements-dev.txt && pytest -c pyproject.toml 
        /tests/backend/test_ocr_smoke.py -v"
"""

import io
import os
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw, ImageFont

pytestmark = pytest.mark.skipif(
    not os.environ.get("EASYOCR_MODULE_PATH"),
    reason="OCR models are only baked into the API image (EASYOCR_MODULE_PATH is not set)",
)

# Restricted character set for room signs: digits, A-Z, space, hyphen (Spec, Stage 2).
ALLOWLIST = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ -"


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
    """English reader with downloads disabled: it only works if the models are in the image."""
    import easyocr

    return easyocr.Reader(["en"], gpu=False, download_enabled=False)


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
    pieces = reader.readtext(sign_png("SCI 333"), allowlist=ALLOWLIST, detail=0)
    assert "".join(pieces).replace(" ", "").replace("-", "") == "SCI333"


def test_allowlist_blocks_other_characters(reader: Any) -> None:
    pieces = reader.readtext(sign_png("Room 12, ok?"), allowlist=ALLOWLIST, detail=0)
    assert all(set(piece) <= set(ALLOWLIST) for piece in pieces)
