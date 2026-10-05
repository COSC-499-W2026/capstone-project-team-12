"""Measure EasyOCR on real room-sign photos inside the API image (issue #120).

Reads every photo listed in ``labels.csv`` (columns: ``file,expected``, expected as it is
normalised for matching, for example ``COM316``), runs the same steps the spec gives for the photo
pipeline (resize to 1600 px, grayscale, auto-contrast, EasyOCR with the restricted character set)
and prints a Markdown report with the time per photo and the memory use.

The utils/ folder is not copied into the image, so mount it next to the photos. From the repo root:

    docker compose run --rm --no-deps \
        -v ./utils:/utils:ro -v /path/to/photos:/photos:ro api \
        python /utils/ocr_spike.py /photos

Memory is the peak resident set size of this process (Linux, so run it in the container).
"""

import argparse
import csv
import os
import platform
import re
import resource
import statistics
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
from PIL import Image, ImageOps

# Restricted character set for room signs: digits, A-Z, space, hyphen (Spec, Stage 2).
ALLOWLIST = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ -"
MAX_EDGE_PX = 1600  # the app resizes photos to at most this on the long edge (Spec, Stage 2)
ROOM_NUMBER = re.compile(r"[A-Z]{3}\d{3,4}")  # 3 letters + 3 or 4 digits (Spec, Stage 2)
SEPARATORS = re.compile(r"[ -]")
KIB_PER_MIB = 1024
COLUMNS = (
    "photo",
    "expected",
    "read",
    "confidence",
    "correct",
    "boxes",
    "prepare s",
    "OCR s",
    "peak RSS MiB",
)


class Candidate(NamedTuple):
    text: str
    confidence: float


class PhotoResult(NamedTuple):
    name: str
    expected: str
    best: Candidate | None
    boxes: int
    prepare_s: float
    ocr_s: float
    peak_rss_mb: float


def peak_rss_mb() -> float:
    """Peak resident memory of this process so far, in MiB (ru_maxrss is KiB on Linux)."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / KIB_PER_MIB


def prepare(path: Path) -> np.ndarray:
    """Resize, grayscale and auto-contrast a photo, as the verification pipeline will."""
    with Image.open(path) as photo:
        image = ImageOps.exif_transpose(photo)
        image.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX))
        return np.asarray(ImageOps.autocontrast(image.convert("L")))


def normalise(text: str) -> str:
    """Uppercase and remove spaces and hyphens."""
    return SEPARATORS.sub("", text.upper())


def candidates(boxes: Sequence[tuple[Any, str, float]]) -> list[Candidate]:
    """Room-number candidates from single text boxes and from neighbouring boxes joined.

    The building letters and the number can be read as separate boxes, so each pair of
    neighbouring boxes is also tried, with the lower of the two confidences.
    """
    texts = [(normalise(text), confidence) for _, text, confidence in boxes]
    singles = [Candidate(text, confidence) for text, confidence in texts]
    pairs = [
        Candidate(first + second, min(first_conf, second_conf))
        for (first, first_conf), (second, second_conf) in zip(texts, texts[1:], strict=False)
    ]
    return [item for item in singles + pairs if ROOM_NUMBER.fullmatch(item.text)]


def read_photo(reader: Any, path: Path, expected: str) -> PhotoResult:
    started = time.perf_counter()
    image = prepare(path)
    prepared = time.perf_counter()
    boxes = reader.readtext(image, allowlist=ALLOWLIST)
    finished = time.perf_counter()
    found = candidates(boxes)
    best = max(found, key=lambda item: item.confidence, default=None)
    return PhotoResult(
        path.name,
        expected,
        best,
        len(boxes),
        prepared - started,
        finished - prepared,
        peak_rss_mb(),
    )


def load_labels(path: Path) -> list[tuple[str, str]]:
    with path.open(newline="") as handle:
        return [(row["file"], normalise(row["expected"])) for row in csv.DictReader(handle)]


def format_row(result: PhotoResult) -> str:
    best = result.best
    text = best.text if best else "-"
    confidence = f"{best.confidence:.2f}" if best else "-"
    correct = "yes" if text == result.expected else "no"
    return (
        f"| {result.name} | {result.expected} | {text} | {confidence} | {correct} | "
        f"{result.boxes} | {result.prepare_s:.2f} | {result.ocr_s:.2f} | {result.peak_rss_mb:.0f} |"
    )


def format_report(results: Sequence[PhotoResult], header: Sequence[str]) -> str:
    columns = "| " + " | ".join(COLUMNS) + " |"
    rule = "|" + "---|" * len(COLUMNS)
    correct = sum(1 for item in results if item.best and item.best.text == item.expected)
    ocr_times = sorted(item.ocr_s for item in results)
    summary = [
        f"- correct: {correct} of {len(results)}",
        f"- OCR seconds per photo: median {statistics.median(ocr_times):.2f}, "
        f"min {ocr_times[0]:.2f}, max {ocr_times[-1]:.2f}",
        f"- peak RSS after all photos: {results[-1].peak_rss_mb:.0f} MiB",
    ]
    rows = [format_row(item) for item in results]
    return "\n".join([*header, "", columns, rule, *rows, "", *summary])


def environment_header(load_s: float, warmup_s: float, loaded_rss_mb: float) -> list[str]:
    import easyocr
    import torch

    return [
        f"- machine: {platform.machine()}, {os.cpu_count()} CPUs, "
        f"{torch.get_num_threads()} torch threads",
        f"- easyocr {easyocr.__version__}, torch {torch.__version__}",
        f"- reader load (models from disk, downloads disabled): {load_s:.2f} s",
        f"- first read of the first photo (warm-up, not in the table): {warmup_s:.2f} s",
        f"- peak RSS after loading the reader: {loaded_rss_mb:.0f} MiB",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("photo_dir", type=Path, help="folder with the photos and labels.csv")
    args = parser.parse_args()

    import easyocr

    started = time.perf_counter()
    reader = easyocr.Reader(["en"], gpu=False, download_enabled=False)
    load_s = time.perf_counter() - started
    loaded_rss_mb = peak_rss_mb()

    labels = load_labels(args.photo_dir / "labels.csv")
    first_photo = args.photo_dir / labels[0][0]
    warmup_s = read_photo(reader, first_photo, labels[0][1]).ocr_s

    results = [read_photo(reader, args.photo_dir / name, expected) for name, expected in labels]
    print(format_report(results, environment_header(load_s, warmup_s, loaded_rss_mb)))


if __name__ == "__main__":
    main()
