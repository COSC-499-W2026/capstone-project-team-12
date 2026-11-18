"""Reads the text on a sign photo with EasyOCR (DFD 3.4).

Rules from the spec (Game Flow > Stage 2: Verification, step 6, and Tech Stack):

- OCR reads the enhanced copy of the photo (grayscale with auto-contrast, made by
  `images.prepare_photo`) with EasyOCR's English reader, restricted to digits, A-Z, space and
  hyphen.
- OCR runs off the event loop, in the FastAPI thread pool: a read takes seconds on CPU, so only
  call `read_text` from a plain `def` route or another worker thread.

One reader serves the whole process. Loading it takes about 850 MiB (docs/ocr-spike.md), so a
reader per request or per thread would not fit the server. It is built on first use, with
downloads disabled: the models are baked into the API image, and a request must never fetch
them. Importing this module loads neither the models nor torch (importing EasyOCR alone takes
about 3 s), so the test suite and the CLI jobs never pay for OCR they do not run. A lock makes
two uploads that arrive together after a restart build one reader, not two.

How many reads run at once is the caller's limit (spec: at most 2), not this module's.

Second pass (proposed, not in the spec, team to confirm; measured on 82 campus sign photos,
Nov 11; the decision table is unchanged, only what it is given to read): when the enhanced
copy does not give a MATCH, the caller may read `local_contrast(enhanced)` as well and keep the
better result. Auto-contrast stretches the whole photo at once, so a dim sign next to a bright
window stays dim; CLAHE stretches each part of the photo on its own. It raised MATCH from 53 to
59 of 70 readable photos, with no wrong-room MATCH, and was needed on about 1 photo in 4. Each
call to `read_text` is one read; deciding when to read again is the caller's job.
"""

import threading
from functools import lru_cache
from typing import Any

import cv2
import numpy as np
from PIL import Image

from app.verification.matching import TextBox

ALLOWLIST = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ -"  # the characters of a room sign
LANGUAGES = ["en"]
# CLAHE settings of the second pass, as measured; change them only with a new measurement
CLAHE_CLIP_LIMIT = 2.0
CLAHE_TILES = (8, 8)

_reader_lock = threading.Lock()


def read_text(image: Image.Image) -> list[TextBox]:
    """Every piece of text found in a grayscale (`L`) image, in the reader's order.

    EasyOCR takes a 2-D array as a grayscale image. Any other mode is refused: EasyOCR would
    read an RGB array as BGR and cannot read a 1-bit one. Its confidences are numpy floats; they
    are returned as plain floats so they can be stored and compared like any other number.
    """
    _require_grayscale(image)
    found = _reader().readtext(np.asarray(image), allowlist=ALLOWLIST)
    return [TextBox(text, float(confidence)) for _box, text, confidence in found]


def local_contrast(image: Image.Image) -> Image.Image:
    """The copy for the second pass: a grayscale (`L`) image with CLAHE applied, same size.

    CLAHE (contrast-limited adaptive histogram equalisation) splits the image into a grid of
    tiles (`CLAHE_TILES`) and stretches the contrast of each one, limited (`CLAHE_CLIP_LIMIT`)
    so flat areas do not turn into noise.
    """
    _require_grayscale(image)
    clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP_LIMIT, tileGridSize=CLAHE_TILES)
    return Image.fromarray(clahe.apply(np.asarray(image)))


def _require_grayscale(image: Image.Image) -> None:
    if image.mode != "L":
        raise ValueError(f"OCR reads grayscale (L) images, not {image.mode}")


def _reader() -> Any:
    with _reader_lock:
        return _load_reader()


@lru_cache
def _load_reader() -> Any:
    import easyocr  # here, not at the top: it imports torch, see the module docstring

    return easyocr.Reader(LANGUAGES, gpu=False, download_enabled=False)
