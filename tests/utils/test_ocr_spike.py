"""Unit tests for the model-free parts of utils/ocr_spike.py (issue #120).

Fixed text boxes stand in for EasyOCR output, so these run in CI without the models. The matching
rules follow the spec's photo pipeline: normalise, keep [A-Z]{3}\\d{3,4}, join neighbouring boxes.
"""

from pathlib import Path
from typing import Any

import pytest
from ocr_spike import Candidate, PhotoResult, candidates, format_row, load_labels, normalise


def boxes(*items: tuple[str, float]) -> list[tuple[Any, str, float]]:
    """EasyOCR-style (bounding box, text, confidence) tuples; the box is not used."""
    return [(None, text, confidence) for text, confidence in items]


def result(best: Candidate | None) -> PhotoResult:
    """A photo result with fixed timings, for testing the table row."""
    return PhotoResult("sign.jpg", "COM316", best, 59, 0.11, 3.79, 1629.4)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("COM 316", "COM316"),  # space removed
        ("com-316", "COM316"),  # lowercase and hyphen
        ("LIB  -  332", "LIB332"),  # several separators in a row
        ("", ""),  # nothing to normalise
    ],
)
def test_normalise_uppercases_and_drops_spaces_and_hyphens(text: str, expected: str) -> None:
    assert normalise(text) == expected


def test_candidates_reads_a_room_number_from_one_box() -> None:
    # Happy path: the whole sign text is in a single box.
    assert candidates(boxes(("COM 316", 0.92))) == [Candidate("COM316", 0.92)]


def test_candidates_joins_building_letters_and_number_from_neighbouring_boxes() -> None:
    # The building letters and the number are read as two boxes (spec: join adjacent boxes).
    found = candidates(boxes(("COM", 0.9), ("316", 0.8)))
    assert found == [Candidate("COM316", 0.8)]  # the lower of the two confidences


def test_candidates_accepts_four_digit_room_numbers() -> None:
    # One building uses 3 letters + 4 digits (spec: Locations).
    assert candidates(boxes(("ABC 1234", 0.7))) == [Candidate("ABC1234", 0.7)]


# Negative cases: none of these may produce a room-number candidate.
@pytest.mark.parametrize(
    "items",
    [
        [("MEETING ROOM", 0.99), ("NOTICE TO USERS", 0.9)],
        [("316", 0.9)],
        [("COM", 0.9)],
        [("COM 31", 0.9)],
        [("COM 31622", 0.9)],
        [("COM", 0.9), ("MEETING", 0.9), ("316", 0.9)],  # letters and number not neighbours
        [],
    ],
    ids=[
        "text only",
        "number only",
        "letters only",
        "2 digits",
        "5 digits",
        "not adjacent",
        "empty",
    ],
)
def test_candidates_ignores_text_that_is_not_a_room_number(items: list[tuple[str, float]]) -> None:
    assert candidates(boxes(*items)) == []


def test_format_row_marks_a_correct_read() -> None:
    # Confidence has 2 decimals, times 2 decimals, memory is rounded to whole MiB.
    row = format_row(result(Candidate("COM316", 0.92)))
    assert row == "| sign.jpg | COM316 | COM316 | 0.92 | yes | 59 | 0.11 | 3.79 | 1629 |"


def test_format_row_marks_a_wrong_read() -> None:
    # Negative: a read that differs from the expected room number is not correct.
    assert "| COM312 | 0.80 | no |" in format_row(result(Candidate("COM312", 0.8)))


def test_format_row_shows_dashes_when_nothing_was_read() -> None:
    # Negative: no candidate means no read and no confidence.
    assert "| - | - | no |" in format_row(result(None))


def test_load_labels_normalises_the_expected_room_numbers(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    # Expected values are normalised the same way as OCR text, so labels can be written freely.
    labels.write_text("file,expected\na.jpg,com 316\nb.jpg,LIB-332\n")
    assert load_labels(labels) == [("a.jpg", "COM316"), ("b.jpg", "LIB332")]


@pytest.mark.parametrize("content", ["", "file,expected\n"], ids=["empty file", "header only"])
def test_load_labels_rejects_a_file_without_photos(tmp_path: Path, content: str) -> None:
    # Negative: with no rows the report would crash on labels[0] and on the median of no times.
    labels = tmp_path / "labels.csv"
    labels.write_text(content)
    with pytest.raises(ValueError, match="no photos listed"):
        load_labels(labels)
